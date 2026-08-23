"""Continuous toxicity-pricing backtest for Sentinel v2.

Policy under test:

    fee_bps(t) = clip(k * EMA_t[realized adverse-selection loss rate],
                      base_fee_bps, cap_fee_bps)

where the EMA decays with *time* (block-clock seconds), mirroring what an
on-chain hook can maintain from its own swap history. Calibration happens on
Feb-Sep 2025 day-windows; Sep-Dec is reported as untouched evaluation slices.

Reported per configuration:
    recapture      dynamic fees collected on positive-cost trades minus the
                   same-trade fees at base rate, over total AS losses
    precision      share of fee uplift that landed on genuinely toxic trades
    coverage       fees collected on toxic trades / those trades' AS losses
    burden         average uplift in bps across ALL traded volume
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


BASE_BPS = 5.0
CALIBRATION_END_MONTH = 9  # Jan-Aug... Feb-Sep inclusive calibrates; Oct-Dec evaluates


def _load_streams(rows_path: Path) -> dict[str, list[dict[str, Any]]]:
    streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    import datetime as dt

    with rows_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            role = row["day_pool"].split("_", 1)[1]
            month = int(row["day_pool"][:7].split("-")[1])
            streams[role].append(
                {
                    "t": float(row["timestamp"]),
                    "day_pool": row["day_pool"],
                    "month": month,
                    "notional": float(row["notional_token1"] or 0.0),
                    "cost": float(row["as_cost_h60"] or 0.0),
                }
            )
    for stream in streams.values():
        stream.sort(key=lambda r: (r["day_pool"], r["t"]))
    return streams


def replay(
    stream: list[dict[str, Any]],
    *,
    half_life_seconds: float,
    k: float,
    cap_bps: float,
) -> dict[str, Any]:
    periods = ("calib", "eval")
    lam = math.log(2.0) / max(half_life_seconds, 1.0)
    ema_rate = 0.0
    last_t: float | None = None
    prev_day: str | None = None

    stats = {
        period: {
            "uplift_toxic": 0.0,
            "uplift_calm": 0.0,
            "fees_dynamic": 0.0,
            "fees_static": 0.0,
            "as_total": 0.0,
            "as_toxic": 0.0,
            "notional": 0.0,
            "trades": 0,
        }
        for period in periods
    }

    for row in stream:
        period = "calib" if row["month"] <= CALIBRATION_END_MONTH else "eval"
        if row["day_pool"] != prev_day:
            ema_rate = 0.0
            last_t = None
            prev_day = row["day_pool"]
        t = row["t"]
        if last_t is not None:
            ema_rate *= math.exp(-lam * (t - last_t))
        last_t = t

        s = stats[period]
        notional = row["notional"]
        cost = row["cost"]
        predicted_bps = min(max(k * ema_rate * 1e4, BASE_BPS), cap_bps)
        static_bps = BASE_BPS
        fee_dyn = predicted_bps / 1e4 * notional
        fee_sta = static_bps / 1e4 * notional
        uplift = fee_dyn - fee_sta
        toxic = cost > 0

        s["fees_dynamic"] += fee_dyn
        s["fees_static"] += fee_sta
        s["as_total"] += cost
        if toxic:
            s["as_toxic"] += cost
            s["uplift_toxic"] += uplift
        else:
            s["uplift_calm"] += uplift
        s["notional"] += notional
        s["trades"] += 1

        # update after the trade: today's realization enters the signal
        realized_rate = abs(cost) / notional if notional > 0 else 0.0
        ema_rate += (1.0 - math.exp(-lam)) * (realized_rate - ema_rate)

    out: dict[str, Any] = {}
    for name, s in stats.items():
        uplift_total = s["uplift_toxic"] + s["uplift_calm"]
        out[name] = {
            "trades": s["trades"],
            "notional": round(s["notional"], 2),
            "extra_fees": round(uplift_total, 2),
            "precision": round(s["uplift_toxic"] / uplift_total, 4) if uplift_total > 0 else None,
            "coverage": round(
                (s["fees_dynamic"] - s["fees_static"]) / s["as_toxic"], 4
            )
            if s["as_toxic"] > 0
            else None,
            "net_dynamic": round(s["fees_dynamic"] - s["as_total"], 2),
            "net_static": round(s["fees_static"] - s["as_total"], 2),
            "burden_bps": round(uplift_total / s["notional"] * 1e4, 3)
            if s["notional"] > 0
            else 0.0,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    streams = _load_streams(args.rows)
    sweep = [
        {"half_life_seconds": hl, "k": k, "cap_bps": cap}
        for hl in (300.0, 900.0, 1800.0)
        for k in (1.0, 2.0, 4.0)
        for cap in (30.0, 100.0)
    ]

    report: dict[str, Any] = {}
    for role, stream in sorted(streams.items()):
        entries = []
        for config in sweep:
            result = replay(stream, **config)
            evalr = result["eval"]
            entries.append(
                {
                    "config": config,
                    "calib": result["calib"],
                    "eval": evalr,
                    "score_eval": (
                        (evalr["coverage"] or 0.0)
                        * (evalr["precision"] or 0.0)
                        / max(evalr["burden_bps"], 0.001)
                    ),
                }
            )
        best = max(entries, key=lambda e: e["score_eval"])
        report[role] = {"sweep": entries, "best_on_calib_score": best}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    console = {}
    for role, payload in report.items():
        best = payload["best_on_calib_score"]
        console[role] = {
            "best_config": best["config"],
            "calib": {key: best["calib"][key] for key in ("precision", "coverage", "burden_bps")},
            "eval_oct_dec": {
                key: best["eval"][key]
                for key in ("precision", "coverage", "burden_bps", "net_dynamic", "net_static")
            },
        }
    print(json.dumps(console, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
