"""Validate the on-chain-deployable self-drift signal against labeled losses.

The backtested Gate 1 policy used external reference prices, which a hook
cannot read. The deployable proxy: after a swap in direction d, subsequent
pool-price drift d × ΔP/P (measured at the NEXT swap, before it executes)
indicates informed flow. This script computes the EMA of |drift| directly from
raw ingested events and correlates it with reference-priced window losses.

Development evidence for Sentinel v2; not a holdout result.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path


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
    parser.add_argument("--windows-root", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--role", default="M2_eth_usdc")
    parser.add_argument("--half-life-seconds", type=float, default=300.0)
    parser.add_argument("--lookback-seconds", type=float, default=60.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    study_pool = plan["measurable_pools"][args.role]
    lam = math.log(2.0) / args.half_life_seconds

    # bucket losses for the study pool, recomputed from labeled rows
    bucket_loss: dict[tuple[str, int], float] = defaultdict(float)
    with args.rows.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["day_pool"].endswith(args.role):
                key = (row["day_pool"].split("_")[0], int(float(row["timestamp"]) // 300))
                bucket_loss[key] += row["as_cost_h60"] or 0.0

    bucket_signal: dict[tuple[str, int], list[float]] = defaultdict(list)
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

        ema_signed = 0.0
        ema_lookback = 0.0
        samples: list[tuple[float, int]] = []  # (t, sqrtP) ring for lookback vol
        prev_t: float | None = None
        for row in swaps:
            t = float(row["block_timestamp"])
            sqrt_p = int(row["sqrt_price_x96"])
            if sqrt_p == 0:
                continue
            if prev_t is not None:
                dt = max(0.0, t - prev_t)
                decay = math.exp(-lam * dt)
                weight = 1.0 - decay
                # lookback realized vol: |sqrtP_now - sqrtP(t - L)| / sqrtP(t - L)
                target_t = t - args.lookback_seconds
                ref = None
                for st, sp in reversed(samples):
                    if st <= target_t:
                        ref = (st, sp)
                        break
                if ref is None and samples:
                    ref = samples[0]
                if ref is not None and ref[1] > 0 and t - ref[0] >= args.lookback_seconds * 0.5:
                    vol_obs = abs(sqrt_p - ref[1]) / ref[1]
                    ema_lookback = decay * ema_lookback + weight * vol_obs
            bucket_signal[(day, int(t // 300))].append((ema_signed, ema_lookback, 0.0))
            samples.append((t, sqrt_p))
            if len(samples) > 64:
                samples = samples[-64:]
            prev_t = t

    xs: list[float] = []
    xs_pos: list[float] = []
    ys: list[float] = []
    for (day, slot), signals in bucket_signal.items():
        loss = bucket_loss.get((day, slot))
        if loss is None or not signals:
            continue
        xs.append(sum(s[0] for s in signals) / len(signals))
        xs_pos.append(sum(s[1] for s in signals) / len(signals))
        ys.append(loss)
    result = {
        "role": args.role,
        "half_life_seconds": args.half_life_seconds,
        "lookback_seconds": args.lookback_seconds,
        "buckets_joined": len(xs),
        "spearman_lookbackvol_vs_loss": round(_spearman(xs_pos, ys), 4),
        "spearman_lookbackvol_vs_absloss": round(_spearman(xs_pos, [abs(y) for y in ys]), 4),
        "note": "deployable self-price lookback-vol EMA vs external-reference labels; dev evidence only",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
