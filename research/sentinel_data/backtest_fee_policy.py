"""Offline backtest: does a loss-reactive dynamic fee recapture adverse selection?

Replays every labeled validation trade through a simulated fee policy and
compares net LP economics against static-fee baselines:

    LP net = fees collected - adverse-selection losses

Signals use only information available before each trade (an EWMA of recently
realized adverse-selection cost), mirroring what an on-chain hook could
compute from its own swap history. This is development tooling for Sentinel
v2; results on calendar-2025 data are development evidence, not holdout proof.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    idx = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[idx]


def backtest_role(
    trades: list[dict[str, Any]],
    *,
    ema_half_life_trades: int,
    elevated_fee_bps: float,
    base_fee_bps: float,
    threshold_quantile: float,
) -> dict[str, Any]:
    """Replay one pool's trades chronologically through the fee policy."""

    lam = math.log(2.0) / max(1, ema_half_life_trades)
    # pass 1: calibrate the elevation threshold on this stream's EMA levels
    ema = 0.0
    ema_levels: list[float] = []
    events: list[tuple[float, str, float, float]] = []
    for row in trades:
        cost = abs(float(row["as_cost_h60"] or 0.0))
        notional = float(row["notional_token1"] or 0.0)
        rate = cost / notional if notional > 0 else 0.0
        events.append((float(row["timestamp"]), row["day_pool"], notional, rate))
        ema_levels.append(ema)
        weight = 1.0 - math.exp(-lam)
        ema = ema + weight * (rate - ema)
    threshold = _percentile(sorted(ema_levels), threshold_quantile)

    # pass 2: replay with fees
    ema = 0.0
    dyn_fees = 0.0
    static_fees: dict[str, float] = defaultdict(float)
    elevated_swaps = 0
    total_notional = 0.0
    total_cost = 0.0
    prev_day_pool = None
    for (timestamp, day_pool, notional, rate) in events:
        if day_pool != prev_day_pool:
            ema = 0.0  # reset at day boundaries: no overnight memory
        prev_day_pool = day_pool
        fee_rate_bps = elevated_fee_bps if ema > threshold else base_fee_bps
        if ema > threshold:
            elevated_swaps += 1
        fee = notional * fee_rate_bps / 10_000.0
        dyn_fees += fee
        for bps in STATIC_BASELINES_BPS:
            static_fees[str(bps)] += notional * bps / 10_000.0
        total_notional += notional
        total_cost += notional * rate
        weight = 1.0 - math.exp(-lam)
        ema = ema + weight * (rate - ema)

    static_nets = {
        bps: static_fees[str(bps)] - total_cost for bps in STATIC_BASELINES_BPS
    }
    return {
        "trades": len(events),
        "total_notional": total_notional,
        "total_adverse_selection": total_cost,
        "dynamic_fees_collected": dyn_fees,
        "elevated_swap_share": elevated_swaps / len(events) if events else 0.0,
        "static_fees_collected": dict(static_fees),
        "net_static": static_nets,
        "net_dynamic": dyn_fees - total_cost,
        "improvement_vs_best_static": (dyn_fees - total_cost)
        - max(static_nets.values()),
        "avg_fee_paid_bps_dynamic": (
            dyn_fees / total_notional * 10_000.0 if total_notional > 0 else 0.0
        ),
    }


STATIC_BASELINES_BPS = [1.0, 5.0, 30.0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--configs", type=Path, default=None,
        help="optional JSON file with a list of policy configs to sweep",
    )
    args = parser.parse_args(argv)

    streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with args.rows.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            streams[row["role"] if "role" in row else row["day_pool"].split("_", 1)[1]].append(
                {
                    "timestamp": row["timestamp"],
                    "day_pool": row["day_pool"],
                    "as_cost_h60": row["as_cost_h60"],
                    "notional_token1": row["notional_token1"],
                }
            )
    for rows in streams.values():
        rows.sort(key=lambda r: (r["day_pool"], r["timestamp"], r.get("log_index", 0)))

    default_sweep = [
        {"ema_half_life_trades": 25, "elevated_fee_bps": 30, "base_fee_bps": 5, "threshold_quantile": 0.6},
        {"ema_half_life_trades": 50, "elevated_fee_bps": 30, "base_fee_bps": 5, "threshold_quantile": 0.6},
        {"ema_half_life_trades": 50, "elevated_fee_bps": 100, "base_fee_bps": 5, "threshold_quantile": 0.75},
        {"ema_half_life_trades": 100, "elevated_fee_bps": 100, "base_fee_bps": 5, "threshold_quantile": 0.75},
        {"ema_half_life_trades": 200, "elevated_fee_bps": 300, "base_fee_bps": 5, "threshold_quantile": 0.9},
    ]
    sweep = (
        json.loads(args.configs.read_text(encoding="utf-8"))
        if args.configs
        else default_sweep
    )

    report: dict[str, Any] = {}
    for role, trades in sorted(streams.items()):
        role_results = []
        for config in sweep:
            result = backtest_role(trades, **config)
            role_results.append({"config": config, **result})
        best = max(role_results, key=lambda r: r["net_dynamic"])
        baseline_key = max(
            r["config"]["base_fee_bps"] for r in role_results
        )
        report[role] = {
            "trades": len(trades),
            "total_adverse_selection_units": sum(
                abs(float(r["as_cost_h60"] or 0.0)) for r in trades
            ),
            "sweep": role_results,
            "best_config_summary": {
                "config": best["config"],
                "net_dynamic": best["net_dynamic"],
                "improvement_vs_same_base_static": best["net_dynamic"]
                - best["net_static"].get(
                    str(best["config"]["base_fee_bps"]),
                    best["net_static"][sorted(best["net_static"])[0]],
                ),
                "elevated_share": best["elevated_swap_share"],
                "avg_fee_paid_bps_dynamic": best["avg_fee_paid_bps_dynamic"],
            },
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    console = {}
    for role, payload in report.items():
        console[role] = {
            "adverse_selection": round(payload["total_adverse_selection_units"], 2),
            "best_net_dynamic": round(payload["best_config_summary"]["net_dynamic"], 2),
            "elevated_share": round(payload["best_config_summary"]["elevated_share"], 3),
            "avg_dyn_fee_bps": round(payload["best_config_summary"]["avg_fee_paid_bps_dynamic"], 1),
        }
    print(json.dumps(console, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
