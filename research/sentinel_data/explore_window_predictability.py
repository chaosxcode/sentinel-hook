"""Exploratory window-level predictability diagnostics (NOT a Gate 1 result).

Streams the full labeled-trade table and aggregates it into 5-minute buckets
per pool-day, then asks: do pre-bucket signals (computed strictly from earlier
buckets) correlate with the current bucket's adverse-selection loss?

Everything here is development evidence for a possible Sentinel v2
preregistration. Nothing in this module feeds the terminated Gate 1 verdict.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


BUCKET_SECONDS = 300
TRAIL_BUCKETS = 12  # one hour of history


def _spearman(x: list[float], y: list[float]) -> float:
    pairs = [(a, b) for a, b in zip(x, y) if not (math.isnan(a) or math.isnan(b))]
    n = len(pairs)
    if n < 10:
        return float("nan")

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den > 0 else float("nan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    opener = gzip.open if str(args.rows).endswith(".gz") else open
    buckets: dict[tuple[str, int], dict[str, float]] = {}
    with opener(args.rows, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = (row["day_pool"], int(float(row["timestamp"]) // BUCKET_SECONDS))
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "loss": 0.0,
                    "notional": 0.0,
                    "signed": 0.0,
                    "vol": 0.0,
                    "vol_n": 0.0,
                    "dev_last": float("nan"),
                    "count": 0.0,
                    "t": float(key[1]),
                }
                buckets[key] = bucket
            cost = row["as_cost_h60"] or 0.0
            notional = row["notional_token1"] or 0.0
            bucket["loss"] += cost
            bucket["notional"] += notional
            bucket["signed"] += float(row["direction"]) * notional
            vol = row.get("vol30")
            if vol is not None and not (isinstance(vol, float) and math.isnan(vol)):
                bucket["vol"] += vol
                bucket["vol_n"] += 1
            dev = row.get("dev")
            if dev is not None and not (isinstance(dev, float) and math.isnan(dev)):
                bucket["dev_last"] = dev
            bucket["count"] += 1

    series: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    for (day_pool, slot), bucket in buckets.items():
        series[day_pool][slot] = bucket

    rows_out: list[dict[str, float]] = []
    for day_pool, slots in series.items():
        ordered_slots = sorted(slots)
        for position, slot in enumerate(ordered_slots):
            history = [slots[s] for s in ordered_slots[max(0, position - TRAIL_BUCKETS) : position]]
            if not history:
                continue
            cur = slots[slot]
            prev = history[-1]
            trail_loss = sum(h["loss"] for h in history[-3:])
            trail_signed = sum(h["signed"] for h in history)
            trail_total = sum(h["notional"] for h in history)
            vols = [h["vol"] / h["vol_n"] for h in history if h["vol_n"] > 0]
            rows_out.append(
                {
                    "target_loss": cur["loss"],
                    "target_abs_loss": abs(cur["loss"]),
                    "target_loss_rate": cur["loss"] / cur["notional"] if cur["notional"] > 0 else float("nan"),
                    "prev_loss": prev["loss"],
                    "trail_loss3": trail_loss,
                    "trail_flow_imbalance": trail_signed / trail_total if trail_total > 0 else float("nan"),
                    "trail_vol_mean": sum(vols) / len(vols) if vols else float("nan"),
                    "prev_dev": prev["dev_last"],
                    "prev_count": prev["count"],
                    "cur_notional": cur["notional"],
                    "has_prev": 1.0,
                }
            )

    signals = {
        "prev_loss": ["target_loss", "target_abs_loss"],
        "trail_loss3": ["target_loss", "target_abs_loss"],
        "trail_flow_imbalance": ["target_loss"],
        "trail_vol_mean": ["target_abs_loss"],
        "prev_dev": ["target_loss"],
    }

    results: dict[str, Any] = {"buckets": len(rows_out), "correlations": {}}
    for signal, targets in signals.items():
        for target in targets:
            rho = _spearman(
                [r[signal] for r in rows_out],
                [r[target] for r in rows_out],
            )
            results["correlations"][f"{signal} -> {target}"] = (
                round(rho, 4) if not math.isnan(rho) else None
            )

    # persistence: does loss cluster in time at all? (AR(1) on raw buckets)
    xs: list[float] = []
    ys: list[float] = []
    for day_pool, slots in series.items():
        ordered = sorted(slots)
        for a, b in zip(ordered[:-1], ordered[1:]):
            xs.append(slots[a]["loss"])
            ys.append(slots[b]["loss"])
    results["ar1_loss_autocorr"] = round(_spearman(xs, ys), 4)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
