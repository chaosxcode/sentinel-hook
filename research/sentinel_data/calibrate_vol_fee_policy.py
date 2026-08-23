"""Calibrate the deployable vol-EMA fee policy for Sentinel v2.

Replays labeled M2 trades with fees driven purely by the on-chain-computable
signal (EMA of |sqrtP(t) - sqrtP(t-lookback)| / sqrtP(t-lookback), half-life
300s) and sweeps k / cap. Calibration: Feb-Sep 2025; evaluation: Oct-Dec 2025.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path


BASE_BPS = 5.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-root", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--role", default="M2_eth_usdc")
    parser.add_argument("--lookback-seconds", type=float, default=60.0)
    parser.add_argument("--half-life-seconds", type=float, default=300.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    study_pool = plan["measurable_pools"][args.role]
    lam = math.log(2.0) / args.half_life_seconds

    # labeled trades keyed by (block, log) — unique per chain
    trades: dict[tuple[int, int], dict[str, float]] = {}
    with args.rows.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if not row["day_pool"].endswith(args.role):
                continue
            trades[(row["block_number"], row["log_index"])] = {
                "t": float(row["timestamp"]),
                "day": row["day_pool"].split("_")[0],
                "month": int(row["day_pool"][:7].split("-")[1]),
                "notional": row["notional_token1"] or 0.0,
                "cost": row["as_cost_h60"] or 0.0,
            }

    # per-trade pre-trade vol EMA from raw events
    ema_before: dict[tuple[int, int], float] = {}
    days = sorted(
        p.name for p in args.windows_root.iterdir() if p.is_dir() and p.name.startswith("2025")
    )
    for day in days:
        path = args.windows_root / day / "events.jsonl.gz"
        if not path.exists():
            continue
        raw = gzip.decompress(path.read_bytes())
        swaps = []
        for line in raw.decode("utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if row["event"] == "Swap" and row["pool_id"] == study_pool:
                swaps.append(row)
        swaps.sort(key=lambda r: (r["block_timestamp"], r["log_index"]))
        ema = 0.0
        samples: list[tuple[float, int]] = []
        prev_t: float | None = None
        for row in swaps:
            t = float(row["block_timestamp"])
            sqrt_p = int(row["sqrt_price_x96"])
            if sqrt_p == 0:
                continue
            key = (row["block_number"], row["log_index"])
            if prev_t is not None:
                ema_before[key] = ema  # pre-trade signal
            dt = max(0.0, t - prev_t) if prev_t is not None else 0.0
            decay = math.exp(-lam * dt)
            weight = 1.0 - decay
            target_t = t - args.lookback_seconds
            ref = None
            for st, sp in reversed(samples):
                if st <= target_t:
                    ref = (st, sp)
                    break
            if ref is None and samples:
                ref = samples[0]
            if ref is not None and ref[1] > 0 and t - ref[0] >= args.lookback_seconds * 0.5:
                ema = decay * ema + weight * (abs(sqrt_p - ref[1]) / ref[1])
            samples.append((t, sqrt_p))
            if len(samples) > 64:
                samples = samples[-64:]
            prev_t = t

    # replay fee policies
    sweep = [
        {"k": k, "cap_bps": cap}
        for k in (0.5, 1.0, 2.0, 4.0, 8.0)
        for cap in (30.0, 100.0)
    ]
    ordered = sorted(
        (
            (trades[key], ema_before.get(key, 0.0), key)
            for key in trades
        ),
        key=lambda item: (item[0]["day"], item[0]["t"]),
    )

    results = []
    for config in sweep:
        stats = {
            period: {"uplift_toxic": 0.0, "uplift_calm": 0.0, "fees_dyn": 0.0, "fees_sta": 0.0, "as": 0.0, "nt": 0.0}
            for period in ("calib", "eval")
        }
        prev_day = None
        ema_replay = 0.0
        for trade, signal, _key in ordered:
            if trade["day"] != prev_day:
                ema_replay = signal  # day boundary: adopt the day's opening signal
                prev_day = trade["day"]
            period = "calib" if trade["month"] <= 9 else "eval"
            fee_bps = min(max(config["k"] * signal * 1e4, BASE_BPS), config["cap_bps"])
            fee_dyn = fee_bps / 1e4 * trade["notional"]
            fee_sta = BASE_BPS / 1e4 * trade["notional"]
            uplift = fee_dyn - fee_sta
            s = stats[period]
            s["fees_dyn"] += fee_dyn
            s["fees_sta"] += fee_sta
            s["as"] += trade["cost"]
            s["nt"] += trade["notional"]
            if trade["cost"] > 0:
                s["uplift_toxic"] += uplift
            else:
                s["uplift_calm"] += uplift
            ema_replay = signal
        entry: dict[str, object] = {"config": config}
        for period, s in stats.items():
            uplift_total = s["uplift_toxic"] + s["uplift_calm"]
            entry[period] = {
                "extra_fees": round(uplift_total, 2),
                "precision": round(s["uplift_toxic"] / uplift_total, 4) if uplift_total > 0 else None,
                "coverage": round((s["fees_dyn"] - s["fees_sta"]) / s["as"], 4) if s["as"] > 0 else None,
                "net_dynamic": round(s["fees_dyn"] - s["as"], 2),
                "net_static": round(s["fees_sta"] - s["as"], 2),
                "burden_bps": round(uplift_total / s["nt"] * 1e4, 3) if s["nt"] > 0 else 0.0,
            }
        results.append(entry)

    payload = {
        "role": args.role,
        "lookback_seconds": args.lookback_seconds,
        "half_life_seconds": args.half_life_seconds,
        "base_bps": BASE_BPS,
        "trades": len(ordered),
        "sweep": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for entry in results:
        c, ev = entry["config"], entry["eval"]
        print(
            f"k={c['k']:<4} cap={int(c['cap_bps']):<4} "
            f"eval: net={ev['net_dynamic']:>14,.0f} (static {ev['net_static']:>12,.0f}) "
            f"prec={ev['precision']} cov={ev['coverage']} burden={ev['burden_bps']}bps"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
