"""Gate 2 holdout evaluation — one-shot replay per research/V2_PREREG.md.

Labels the holdout windows with the frozen 60-second reference-priced
adverse-selection cost, replays the frozen Sentinel v2 policy (self-vol EMA
signal, k=4, base 5bps, cap 100bps, half-life 300s), and applies the
pre-registered bars:

  P1  pooled DeltaNet > 0 with clustered-bootstrap 95% CI excluding 0
  P2  DeltaNet > 0 in >= 60% of active pool-months and for >= 1 pool
  P3  average trader burden <= 12 bps

Also reports the diagnostic statistics (precision, coverage, recapture ratio,
AS totals) that give the bars economic context. No parameter search happens
here; everything is frozen in V2_PREREG.md.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .labels import attach_pool_currencies, build_series_from_events, label_day, load_events_gz, price_from_sqrt


BASE_BPS = 5.0
K = 4.0
CAP_BPS = 100.0
HALF_LIFE_SECONDS = 300.0
LOOKBACK_SECONDS = 60.0
BURDEN_LIMIT_BPS = 12.0
BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 20260823


def _pearson_from_totals(t: tuple[float, ...]) -> float:
    n, sx, sy, sxx, syy, sxy = t
    if n < 3:
        return float("nan")
    num = sxy - (sx * sy) / n
    den = math.sqrt(max(sxx - sx * sx / n, 0.0) * max(syy - sy * sy / n, 0.0))
    return num / den if den > 0 else float("nan")


def clustered_bootstrap_uplift(
    rows: list[dict[str, Any]], *, seed: int = BOOTSTRAP_SEED, iterations: int = BOOTSTRAP_ITERATIONS
) -> dict[str, float]:
    """Clustered bootstrap (pool-day) of mean per-trade uplift in bps."""

    uplifts = [row["uplift_bps"] for row in rows]
    clusters: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        clusters.setdefault(str(row["day_pool"]), []).append(index)
    names = sorted(clusters)
    per_cluster = {}
    for name in names:
        idx = clusters[name]
        m = len(idx)
        per_cluster[name] = (
            float(m),
            sum(uplifts[i] for i in idx),
            sum(v * v for v in (uplifts[i] for i in idx)),
        )
    totals = [sum(per_cluster[c][i] for c in names) for i in range(3)]
    n, sx, sxx = totals
    mean = sx / n
    se = math.sqrt(max((sxx - sx * sx / n) / max(n - 1, 1), 0.0) / n)
    rng = random.Random(seed)
    samples = []
    m_clusters = len(names)
    for _ in range(iterations):
        s = 0.0
        cnt = 0.0
        for _c in range(m_clusters):
            m_, sx_, sxx_ = per_cluster[names[rng.randrange(m_clusters)]]
            s += sx_
            cnt += m_
        samples.append(s / cnt)
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[int(0.975 * len(samples)) - 1]
    return {
        "mean_uplift_bps": round(mean, 4),
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
        "excludes_zero": bool(lo > 0 or hi < 0),
        "n_clusters": m_clusters,
        "n_trades": n,
        "se": round(se, 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    cohort = json.loads(
        (args.windows_root.parent.parent / "cohort" / "unichain-core-v1" / "cohort.json").read_text()
    )
    venues_doc = json.loads(
        (args.windows_root.parent.parent / "cohort" / "unichain-core-v1" / "venues.json").read_text()
    )
    tokens = json.loads(
        (args.windows_root.parent.parent / "cohort" / "unichain-core-v1" / "tokens.json").read_text()
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    decimals = {
        row["address"].lower(): int(row["decimals"])
        for row in tokens["probe_results"]
        if row.get("decimals") is not None
    }

    measurable = plan["measurable_pools"]
    venue_map = plan.get("venue_pools", {})
    pairs: dict[str, frozenset[str]] = {}
    for rows in venues_doc["venues"].values():
        for row in rows:
            pairs[row["pool_id"]] = frozenset(row["currencies"])
    for row in venues_doc["quote_deep_venues"]:
        pairs[row["pool_id"]] = frozenset(row["currencies"])
    for row in cohort["core"] + cohort["alternates"]:
        pairs[row["pool_id"]] = frozenset(row["currencies"])
    pair_by_pool = {pid: tuple(sorted(p)) for pid, p in pairs.items()}

    lam = math.log(2.0) / HALF_LIFE_SECONDS
    role_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    diagnostics = {"days_processed": 0, "empty_days": 0}

    for role, study_pool in measurable.items():
        venue_pids = set(venue_map.get(role, []))
        pair_key = pairs[study_pool]
        d0 = decimals[sorted(pair_key)[0].lower()]
        d1 = decimals[sorted(pair_key)[1].lower()]

        for day_dir in sorted(args.windows_root.iterdir()):
            if not day_dir.is_dir() or not (day_dir / "manifest.json").exists():
                continue
            manifest = json.loads((day_dir / "manifest.json").read_text())
            if manifest.get("record_count", 0) == 0:
                diagnostics["empty_days"] += 1
                continue
            events = load_events_gz(day_dir / "events.jsonl.gz")
            attach_pool_currencies(events, pair_by_pool)
            study_swaps = [
                r for r in events if r["event"] == "Swap" and r["pool_id"] == study_pool
            ]
            if not study_swaps:
                diagnostics["empty_days"] += 1
                continue

            series = build_series_from_events(
                [r for r in events if r["event"] == "Swap" and r["pool_id"] in venue_pids],
                decimals,
            )
            labeled = label_day(study_swaps, series, decimals0=d0, horizon_seconds=60.0)

            # deployable signal: EMA of |sqrtP(t) - sqrtP(t-lookback)| / sqrtP
            study_prints = sorted(
                (
                    (float(r["block_timestamp"]), int(r["sqrt_price_x96"]), r["log_index"], r)
                    for r in study_swaps
                ),
                key=lambda item: (item[0], item[2]),
            )
            ema = 0.0
            samples: list[tuple[float, int]] = []
            prev_t: float | None = None
            signal_by_key: dict[tuple[int, int], float] = {}
            for t, sqrt_p, _li, row in study_prints:
                if sqrt_p == 0:
                    continue
                key = (row["block_number"], row["log_index"])
                signal_by_key[key] = ema  # pre-trade signal
                dt = max(0.0, t - prev_t) if prev_t is not None else 0.0
                decay = math.exp(-lam * dt)
                weight = 1.0 - decay
                target_t = t - LOOKBACK_SECONDS
                ref = None
                for st, sp in reversed(samples):
                    if st <= target_t:
                        ref = (st, sp)
                        break
                if ref is None and samples:
                    ref = samples[0]
                if (
                    ref is not None
                    and ref[1] > 0
                    and t - ref[0] >= LOOKBACK_SECONDS * 0.5
                ):
                    ema = decay * ema + weight * (abs(sqrt_p - ref[1]) / ref[1])
                samples.append((t, sqrt_p))
                if len(samples) > 64:
                    samples = samples[-64:]
                prev_t = t

            day = day_dir.name
            for row in labeled:
                if row["status"] != "ok":
                    continue
                key = (row["block_number"], row["log_index"])
                signal = signal_by_key.get(key, 0.0)
                notional = row["notional_token1"] or 0.0
                if notional <= 0:
                    continue
                fee_bps = min(max(K * signal * 1e4, BASE_BPS), CAP_BPS)
                uplift_bps = fee_bps - BASE_BPS
                role_rows[role].append(
                    {
                        "day_pool": f"{day}_{role}",
                        "month": int(day[:7].split("-")[1]),
                        "notional": notional,
                        "cost": row["as_cost_h60"],
                        "fee_bps": fee_bps,
                        "uplift_bps": uplift_bps,
                        "uplift_units": uplift_bps / 1e4 * notional,
                        "signal": signal,
                    }
                )
            diagnostics["days_processed"] += 1

    # ------------------------------------------------------------------
    # aggregate + bars
    # ------------------------------------------------------------------
    per_role: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []
    for role, rows in role_rows.items():
        uplift_units = sum(r["uplift_units"] for r in rows)
        as_total = sum(r["cost"] for r in rows)
        notional = sum(r["notional"] for r in rows)
        fees_static = sum(BASE_BPS / 1e4 * r["notional"] for r in rows)
        fees_dynamic = fees_static + uplift_units
        toxic_uplift = sum(r["uplift_units"] for r in rows if r["cost"] > 0)
        calm_uplift = uplift_units - toxic_uplift
        months: dict[int, dict[str, float]] = defaultdict(lambda: {"uplift": 0.0, "nt": 0.0})
        for r in rows:
            months[r["month"]]["uplift"] += r["uplift_units"]
            months[r["month"]]["nt"] += r["notional"]
        positive_months = sum(1 for m in months.values() if m["uplift"] > 0)
        boot = clustered_bootstrap_uplift(rows)
        per_role[role] = {
            "trades": len(rows),
            "as_total": round(as_total, 2),
            "notional": round(notional, 2),
            "delta_net": round(uplift_units, 2),
            "net_static": round(fees_static - as_total, 2),
            "net_dynamic": round(fees_dynamic - as_total, 2),
            "precision": round(toxic_uplift / uplift_units, 4) if uplift_units > 0 else None,
            "coverage": round(uplift_units / as_total, 4) if as_total > 0 else None,
            "burden_bps": round(uplift_units / notional * 1e4, 3) if notional > 0 else 0.0,
            "positive_month_share": round(positive_months / len(months), 4) if months else None,
            "active_months": len(months),
            "bootstrap": boot,
        }
        all_rows.extend(rows)

    # pooled P1: bootstrap over all rows pooled (clusters keep pool-day identity)
    pooled_boot = clustered_bootstrap_uplift(all_rows)
    pooled_uplift = sum(r["uplift_units"] for r in all_rows)
    pooled_as = sum(r["cost"] for r in all_rows)
    pooled_nt = sum(r["notional"] for r in all_rows)
    pooled_burden = pooled_uplift / pooled_nt * 1e4 if pooled_nt > 0 else 0.0

    p1 = bool(pooled_uplift > 0 and pooled_boot["excludes_zero"])
    month_positive: dict[str, dict[int, bool]] = defaultdict(dict)
    for r in all_rows:
        month_positive[r["day_pool"].split("_")[1]][r["month"]] = (
            month_positive[r["day_pool"].split("_")[1]].get(r["month"], False) or (r["uplift_bps"] > 0)
        )
    active_months = [m for m in {int(r["month"]) for r in all_rows}]
    months_with_trades = 0
    months_positive = 0
    for m in active_months:
        rows_m = [r for r in all_rows if r["month"] == m]
        if len(rows_m) >= 100:
            months_with_trades += 1
            if sum(r["uplift_units"] for r in rows_m) > 0:
                months_positive += 1
    p2 = bool(
        months_with_trades > 0
        and months_positive / months_with_trades >= 0.60
        and any(per_role[role]["delta_net"] > 0 for role in per_role)
    )
    p3 = bool(pooled_burden <= BURDEN_LIMIT_BPS)

    verdict = {
        "plan_sha256": plan["plan_sha256"],
        "policy": {"k": K, "base_bps": BASE_BPS, "cap_bps": CAP_BPS, "half_life_seconds": HALF_LIFE_SECONDS,
                   "lookback_seconds": LOOKBACK_SECONDS},
        "pooled": {
            "delta_net": round(pooled_uplift, 2),
            "as_total": round(pooled_as, 2),
            "recapture_ratio": round(pooled_uplift / pooled_as, 4) if pooled_as > 0 else None,
            "burden_bps": round(pooled_burden, 3),
            "bootstrap": pooled_boot,
        },
        "per_role": per_role,
        "bars": {
            "P1_pass": p1,
            "P2_pass": p2,
            "P3_pass": p3,
            "P2_month_positivity": f"{months_positive}/{months_with_trades}",
            "gate2_pass": bool(p1 and p2 and p3),
        },
        "diagnostics": diagnostics,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(verdict["bars"], indent=2))
    print(json.dumps(verdict["pooled"], indent=2))
    for role, payload in per_role.items():
        print(role, json.dumps({k: payload[k] for k in ("delta_net", "precision", "coverage", "burden_bps")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
