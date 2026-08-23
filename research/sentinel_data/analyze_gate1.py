"""Gate 1 validation analysis driver.

Consumes the frozen measurement plan and the ingested sampled-day windows,
computes adverse-selection labels and pre-swap features per the preregistration,
and evaluates the three Gate 1 bars on calendar-2025 validation data.

Outputs ``gate1-validation-results.json`` plus a human-readable summary. The
locked holdout is never touched here.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .gate1_metrics import (
    clustered_bootstrap_rho,
    ewma_volatility,
    gate1_criteria,
    rolling_flow_imbalance,
    spearman,
    vol_at,
    zscore_within_day,
)
from .labels import (
    attach_pool_currencies,
    build_series_from_events,
    label_day,
    load_events_gz,
    price_from_sqrt,
)


WINDOW_LOSS_BUCKET_SECONDS = 300


def load_pair_maps(
    plan: dict[str, Any], cohort: dict[str, Any], venues_doc: dict[str, Any]
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, frozenset[str]]]:
    """Role -> study pool, role -> venue pools, pool -> currency pair."""

    measurable = plan["measurable_pools"]
    venue_map = plan.get("venue_pools", {})
    pairs: dict[str, frozenset[str]] = {}

    def rows_to_pairs(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            pairs[row["pool_id"]] = frozenset(row["currencies"])

    for role, venue_role in (("M1_usdc_hype", "core2_usdc_hype"), ("M2_eth_usdc", "core3_eth_usdc")):
        rows_to_pairs(venues_doc["venues"][venue_role])
    for row in venues_doc["quote_deep_venues"]:
        if row.get("role") == "quote_eth_usdt0":
            pairs[row["pool_id"]] = frozenset(row["currencies"])
    for row in cohort["alternates"]:
        pairs[row["pool_id"]] = frozenset(row["currencies"])
    for row in cohort["core"]:
        pairs[row["pool_id"]] = frozenset(row["currencies"])

    return measurable, venue_map, pairs


def analyze(days_root: Path, plan: dict[str, Any], tokens: dict[str, Any]) -> dict[str, Any]:
    decimals = {
        row["address"].lower(): int(row["decimals"])
        for row in tokens["probe_results"]
        if row.get("decimals") is not None
    }
    cohort = json.loads((days_root.parent / ".." / "cohort" / "unichain-core-v1" / "cohort.json").read_text())
    venues_doc = json.loads((days_root.parent / ".." / "cohort" / "unichain-core-v1" / "venues.json").read_text())
    measurable, venue_map, pairs = load_pair_maps(plan, cohort, venues_doc)

    pair_by_pool = {pid: tuple(sorted(pair)) for pid, pair in pairs.items()}

    role_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    day_summaries: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    window_losses: dict[str, list[float]] = defaultdict(list)
    diagnostics: dict[str, Any] = {"days_processed": 0, "empty_days": 0}

    for role, study_pool in measurable.items():
        venue_pids = set(venue_map.get(role, []))
        pair_key = pairs[study_pool]
        for day_dir in sorted(days_root.iterdir()):
            if not day_dir.is_dir() or not (day_dir / "manifest.json").exists():
                continue
            events_path = day_dir / "events.jsonl.gz"
            manifest = json.loads((day_dir / "manifest.json").read_text())
            if manifest.get("record_count", 0) == 0:
                diagnostics["empty_days"] += 1
                continue
            events = load_events_gz(events_path)
            attach_pool_currencies(events, pair_by_pool)

            study_swaps = [
                row
                for row in events
                if row["event"] == "Swap" and row["pool_id"] == study_pool
            ]
            if not study_swaps:
                day_summaries[role][day_dir.name] = {"swaps": 0, "labeled_ok": 0, "active": False}
                continue

            d0 = decimals[sorted(pair_key)[0].lower()]
            d1 = decimals[sorted(pair_key)[1].lower()]

            # reference series: same-pair VENUE pools only (prereg section 4
            # excludes the study pool from its own reference price)
            series = build_series_from_events(
                [
                    row
                    for row in events
                    if row["event"] == "Swap" and row["pool_id"] in venue_pids
                ],
                decimals,
            )
            labeled = label_day(study_swaps, series, decimals0=d0, horizon_seconds=60.0)

            # features ------------------------------------------------------
            venue_prints: list[tuple[float, float]] = []
            study_prints: list[tuple[float, float]] = []
            for row in events:
                if row["event"] != "Swap":
                    continue
                c0, c1 = row.get("_currency0"), row.get("_currency1")
                if c0 is None or c1 is None or frozenset((c0, c1)) != pair_key:
                    continue
                price = price_from_sqrt(row["sqrt_price_x96"], d0, d1)
                t = float(row["block_timestamp"])
                if row["pool_id"] == study_pool:
                    study_prints.append((t, price))
                else:
                    venue_prints.append((t, price))
            venue_prints.sort()
            study_prints.sort()
            vt = [p[0] for p in venue_prints]
            vp = [p[1] for p in venue_prints]
            st = [p[0] for p in study_prints]
            sp = [p[1] for p in study_prints]
            vol_times, vols = ewma_volatility(vt, vp, half_life=30.0)

            flows = rolling_flow_imbalance(labeled, window_seconds=300.0)

            enriched: list[dict[str, Any]] = []
            for row, flow in zip(labeled, flows, strict=True):
                if row["status"] != "ok":
                    enriched.append({**row, "vol30": float("nan"), "flow300": flow, "dev": float("nan"), "score": float("nan")})
                    continue
                t = row["timestamp"]
                v30 = vol_at(vol_times, vols, t)
                idx = bisect.bisect_left(st, t) - 1
                pool_mid = sp[idx] if idx >= 0 else float("nan")
                ref_price, _ = series.quote(t)
                dev = (
                    (pool_mid - ref_price) / ref_price
                    if ref_price and not math.isnan(pool_mid)
                    else float("nan")
                )
                enriched.append(
                    {**row, "vol30": v30, "flow300": flow, "dev": dev, "score": float("nan")}
                )

            zs_vol = zscore_within_day([r["vol30"] for r in enriched])
            zs_flow = zscore_within_day([r["flow300"] for r in enriched])
            zs_dev = zscore_within_day([r["dev"] for r in enriched])
            final_rows: list[dict[str, Any]] = []
            for row, a, b, c in zip(enriched, zs_vol, zs_flow, zs_dev, strict=True):
                score = (a + b + c) / 3.0 if row["status"] == "ok" else float("nan")
                final_rows.append({**row, "score": score})
            for row in final_rows:
                row["day_pool"] = f"{day_dir.name}_{role}"
            role_rows[role].extend(final_rows)

            # per-day summary + 5-minute loss windows -----------------------
            usable = [r for r in final_rows if r["status"] == "ok"]
            buckets: dict[int, float] = {}
            for r in usable:
                bucket_index = int(r["timestamp"] // WINDOW_LOSS_BUCKET_SECONDS)
                buckets[bucket_index] = buckets.get(bucket_index, 0.0) + r["as_cost_h60"]
            day_summaries[role][day_dir.name] = {
                "swaps": len(final_rows),
                "labeled_ok": len(usable),
                "active": len(usable) >= 100,
                "as_cost_sum": sum(r["as_cost_h60"] for r in usable),
                "positive_as_cost": bool(sum(r["as_cost_h60"] for r in usable) > 0),
                "total_notional": sum(r["notional_token1"] for r in usable),
            }
            for bucket_value in buckets.values():
                window_losses[f"{day_dir.name}_{role}"].append(bucket_value)
            diagnostics["days_processed"] += 1

    # --------------------------------------------------------------------
    all_active_days: list[dict[str, Any]] = []
    windows_flat: dict[str, list[float]] = {}
    for role, days in day_summaries.items():
        for day, s in days.items():
            if s.get("active"):
                all_active_days.append({"role": role, "day": day, **s})
    for key, values in window_losses.items():
        windows_flat[key] = values

    rho_rows: list[dict[str, Any]] = []
    for role, rows in role_rows.items():
        for row in rows:
            if row["status"] == "ok" and not math.isnan(row["score"]):
                rho_rows.append(
                    {
                        "day_pool": row["day_pool"],
                        "pool_id": row.get("pool_id"),
                        "block_number": row.get("block_number"),
                        "transaction_hash": row.get("transaction_hash"),
                        "log_index": row.get("log_index"),
                        "timestamp": row.get("timestamp"),
                        "direction": row["direction"],
                        "notional_token1": row["notional_token1"],
                        "ref_move": row["ref_move"],
                        "vol30": row.get("vol30"),
                        "flow300": row.get("flow300"),
                        "dev": row.get("dev"),
                        "score": row["score"],
                        "as_cost_h60": row["as_cost_h60"],
                    }
                )
    bootstrap = clustered_bootstrap_rho(rho_rows, iterations=10_000)
    criteria = gate1_criteria(all_active_days, windows_flat, bootstrap)
    per_role_rho = {
        role: spearman(
            [r["score"] for r in rows if r["status"] == "ok"],
            [r["as_cost_h60"] for r in rows if r["status"] == "ok"],
        )
        for role, rows in role_rows.items()
    }

    return {
        "plan_sha256": plan["plan_sha256"],
        "criteria": criteria,
        "bootstrap": bootstrap,
        "per_role_spearman": per_role_rho,
        "active_days_detail": {
            role: {d: s for d, s in days.items()}
            for role, days in day_summaries.items()
        },
        "labeled_swaps_total": len(rho_rows),
        "diagnostics": diagnostics,
        "rows": rho_rows,
        "_window_losses": {k: v for k, v in windows_flat.items()},
    }


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_derived_artifacts(results: dict[str, Any], output_dir: Path) -> None:
    """Compact, committable derivatives of the full label/feature table."""

    import gzip
    import hashlib
    import random

    output_dir.mkdir(parents=True, exist_ok=True)

    windows_payload = json.dumps(results["_window_losses"], sort_keys=True).encode("utf-8")
    windows_path = output_dir / "window-losses.json.gz"
    with open(windows_path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as gz:
            gz.write(windows_payload)

    rng = random.Random(20260823)
    sampled = [row for row in results["rows"] if rng.random() < 0.02]
    sample_lines = b"".join(
        (json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in sampled
    )
    sample_path = output_dir / "rows-sample-2pct.jsonl.gz"
    with open(sample_path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as gz:
            gz.write(sample_lines)

    return {
        "window_losses_sha256": _sha256_of(windows_path),
        "rows_sample_sha256": _sha256_of(sample_path),
        "rows_sample_size": len(sampled),
        "rows_full_count": len(results["rows"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = json.loads((args.windows_root / ".." / "measurement-plan-2025.json").read_text())
        tokens = json.loads(
            (args.windows_root / ".." / ".." / "cohort" / "unichain-core-v1" / "tokens.json").read_text()
        )
        results = analyze(args.windows_root, plan, tokens)
    except Exception as exc:  # CLI boundary
        print(f"analysis failed: {exc}", file=sys.stderr)
        return 1
    payload = {k: v for k, v in results.items() if k != "rows"}

    def sanitize(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    payload = sanitize(payload)
    derived = export_derived_artifacts(results, args.output.parent / "derived")
    payload["derived_artifacts"] = sanitize(derived)
    payload["rows_full_sha256"] = _sha256_of(args.output.with_name(args.output.stem + "-rows.jsonl"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    compact_rows = args.output.with_name(args.output.stem + "-rows.jsonl")
    with compact_rows.open("w", encoding="utf-8") as handle:
        for row in results["rows"]:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(payload["criteria"], indent=2))
    print(json.dumps(payload["per_role_spearman"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
