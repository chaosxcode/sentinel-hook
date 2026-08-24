"""Deterministic Gate 1 measurement plan generation (prereg section 8.3).

Produces the frozen list of sampled validation day-windows: monthly lifetime
probes establish each measurable pool's active months, a seeded RNG selects six
UTC days per active month, and each day is resolved to exact chain blocks by
binary search over block timestamps. The plan is committed before ingestion;
days are never re-drawn after results are seen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .rpc import JsonRpcClient


SCHEMA = "sentinel.measurement-plan.v1"
SEED = 20260823
DAYS_PER_MONTH = 6
SWAP_TOPIC = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"

ROLE_TO_MEASURABLE = {
    "M1_usdc_hype": "core2_usdc_hype",
    "M2_eth_usdc": "core3_eth_usdc",
    "M3_eth_usdt0": "quote_eth_usdt0",
}


def load_pool_sets(cohort_path: Path, venues_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Derive measurable pools and their venues strictly from committed receipts.

    Implements prereg section 8.1: the USDC/SOL core pool has no reference
    venue and is replaced by alternate A1, which is the highest-activity
    native-ETH/USDt0 pool in the venues receipt. For every role the study pool
    is the highest-activity member (ties broken by ascending pool id) and all
    other members become its reference venues.
    """

    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    if cohort.get("schema") != "sentinel.cohort.v1":
        raise ValueError(f"{cohort_path} is not a cohort receipt")
    venues_doc = json.loads(venues_path.read_text(encoding="utf-8"))
    if venues_doc.get("schema") != "sentinel.venues.v1":
        raise ValueError(f"{venues_path} is not a venues receipt")
    if not cohort["alternates"]:
        raise ValueError("cohort receipt has no alternates for the USDC/SOL substitution")

    def pick(rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
        ranked = sorted(rows, key=lambda row: (-row["swap_count_in_window"], row["pool_id"]))
        return ranked[0]["pool_id"], [row["pool_id"] for row in ranked[1:]]

    measurable: dict[str, str] = {}
    venues_by_role: dict[str, list[str]] = {}

    m1_study, m1_venues = pick(venues_doc["venues"]["core2_usdc_hype"])
    measurable["M1_usdc_hype"] = m1_study
    venues_by_role["M1_usdc_hype"] = m1_venues

    m2_study, m2_venues = pick(venues_doc["venues"]["core3_eth_usdc"])
    measurable["M2_eth_usdc"] = m2_study
    venues_by_role["M2_eth_usdc"] = m2_venues

    usdt0_rows = [
        {
            "pool_id": row["pool_id"],
            "swap_count_in_window": row["swap_count_in_window"],
        }
        for row in venues_doc["quote_deep_venues"]
        if row.get("role") == "quote_eth_usdt0"
    ]
    if not usdt0_rows:
        raise ValueError("no native-ETH/USDt0 pools in venues receipt")
    m3_study, m3_venues = pick(usdt0_rows)
    if m3_study not in {row["pool_id"] for row in cohort["alternates"]}:
        raise ValueError(
            "substituted M3 study pool is not the frozen alternate A1; refusing silent change"
        )
    measurable["M3_eth_usdt0"] = m3_study
    venues_by_role["M3_eth_usdt0"] = m3_venues

    return measurable, venues_by_role

VALIDATION_MONTHS_2025 = list(range(1, 13))


def _month_anchor_epoch(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp())


def _epoch_to_utc_day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d")


def estimate_block_for_epoch(head_block: int, head_epoch: int, target_epoch: int) -> int:
    return max(1, head_block - (head_epoch - target_epoch))


def probe_pool_lifetime(
    client: JsonRpcClient,
    pool_manager: str,
    pool_ids: list[str],
    head_block: int,
    head_epoch: int,
    *,
    year: int,
    months: list[int],
    chunk: int = 2000,
) -> dict[str, str]:
    """First calendar month (YYYY-MM) in which any of ``pool_ids`` swaps."""

    topics = [SWAP_TOPIC, [pid.lower() for pid in pool_ids]]
    first_month: dict[str, str] = {}
    for month in months:
        base_epoch = _month_anchor_epoch(year, month)
        # two probes per month (1st and 15th) so a pool born mid-month is not
        # misattributed to the following month.
        for day_offset in (0, 14):
            anchor_epoch = base_epoch + day_offset * 86_400
            anchor_block = estimate_block_for_epoch(head_block, head_epoch, anchor_epoch)
            logs = client.get_logs(
                address=pool_manager,
                topics=topics,
                from_block=anchor_block,
                to_block=min(anchor_block + chunk - 1, head_block),
                chunk_size=chunk,
            )
            for log in logs:
                pid = "0x" + str(log["topics"][1])[2:]
                if pid not in first_month:
                    first_month[pid] = f"{year}-{month:02d}"
    return first_month


def resolve_day_boundaries(
    client: JsonRpcClient,
    day_epochs: list[int],
    head_block: int,
    head_epoch: int,
    *,
    tolerance: int = 5,
) -> dict[str, dict[str, Any]]:
    """Resolve UTC-midnight epochs to exact boundary blocks via binary search."""

    boundaries: dict[str, dict[str, Any]] = {}

    def header(number: int) -> tuple[int, int]:
        block = client.get_blocks([number])[number]
        return block.timestamp, block.hash

    def find_crossing(target_epoch: int) -> dict[str, Any]:
        """Exponential search for a bracket, then bisect to the first block
        whose timestamp is at/after the target epoch."""

        guess = max(1, min(estimate_block_for_epoch(head_block, head_epoch, target_epoch), head_block - 1))
        ts_guess, _ = header(guess)
        if ts_guess < target_epoch:
            lo, hi = guess, min(guess + 1, head_block - 1)
            step = 1000
            while header(hi)[0] < target_epoch and hi < head_block - 1:
                lo = hi
                hi = min(hi + step, head_block - 1)
                step *= 2
        else:
            lo, hi = max(1, guess - 1), guess
            step = 1000
            while header(lo)[0] >= target_epoch and lo > 1:
                hi = lo
                lo = max(1, lo - step)
                step *= 2
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if header(mid)[0] < target_epoch:
                lo = mid
            else:
                hi = mid
        ts_hi, hash_hi = header(hi)
        if abs(ts_hi - target_epoch) > tolerance * 86400:
            raise RuntimeError(
                f"midpoint crossing too far from target: block {hi} ts {ts_hi} vs {target_epoch}"
            )
        return {"epoch": target_epoch, "block": hi, "block_hash": hash_hi, "block_timestamp": ts_hi}

    for epoch in sorted(set(day_epochs)):
        day = _epoch_to_utc_day(epoch)
        boundaries[day] = find_crossing(epoch)
    return boundaries


def generate_plan(
    client_factory: Any,
    config: dict[str, Any],
    output_path: Path,
    cohort_path: Path,
    venues_path: Path,
    *,
    year: int = 2025,
    months: list[int] | None = None,
) -> dict[str, Any]:
    client = client_factory()
    observed_chain_id = client.chain_id()
    if observed_chain_id != int(config["chain_id"]):
        raise RuntimeError(
            f"chain id mismatch: expected {config['chain_id']}, received {observed_chain_id}"
        )
    measurable, venues_by_role = load_pool_sets(cohort_path, venues_path)
    latest_number = int(client.call("eth_blockNumber", []), 16)
    head_receipt = client.get_blocks([latest_number])[latest_number]
    head_block, head_epoch = latest_number, head_receipt.timestamp

    first_month = probe_pool_lifetime(
        client,
        str(config["pool_manager"]),
        sorted(set(measurable.values()) | {v for vs in venues_by_role.values() for v in vs}),
        head_block,
        head_epoch,
        year=year,
        months=months or VALIDATION_MONTHS_2025,
    )

    rng = random.Random(SEED)
    day_selection: dict[str, list[str]] = {}
    all_days: set[int] = set()
    boundary_anchors: set[int] = set()
    for role, pool_id in measurable.items():
        start_month = first_month.get(pool_id)
        if start_month is None:
            day_selection[role] = []
            continue
        sm = int(start_month.split("-")[1])
        chosen: list[str] = []
        for month in range(sm, 13):
            days_in_month = (
                datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=UTC)
                - timedelta(seconds=1)
            ).day
            picks = sorted(rng.sample(range(1, days_in_month + 1), DAYS_PER_MONTH))
            for day in picks:
                epoch = int(datetime(year, month, day, tzinfo=UTC).timestamp())
                if epoch + 86400 <= head_epoch:
                    chosen.append(_epoch_to_utc_day(epoch))
                    all_days.add(epoch)
                    boundary_anchors.add(epoch)
                    boundary_anchors.add(epoch + 86400)
        day_selection[role] = chosen

    boundaries = resolve_day_boundaries(client, sorted(boundary_anchors), head_block, head_epoch)

    hashed_payload = {
        "schema": SCHEMA,
        "name": config["name"] + f"-gate1-plan-{year}",
        "chain_id": int(config["chain_id"]),
        "seed": SEED,
        "days_per_month": DAYS_PER_MONTH,
        "validation_year": year,
        "measurable_pools": measurable,
        "venue_pools": venues_by_role,
        "first_active_month": first_month,
        "selected_days": day_selection,
        "day_boundaries": boundaries,
    }
    receipt = {
        **hashed_payload,
        "generated_at": datetime.now(UTC).isoformat(),
        "plan_sha256": hashlib.sha256(
            json.dumps(hashed_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--months", type=int, nargs="*", default=None,
                        help="calendar months to sample; default = full year")
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--venues", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        env_name = config.get("rpc_url_env")
        url = (
            os.environ[str(env_name)]
            if env_name and os.environ.get(str(env_name))
            else str(config["rpc_url"])
        )
        receipt = generate_plan(
            lambda: JsonRpcClient(url),
            config,
            args.output,
            args.cohort,
            args.venues,
            year=args.year,
            months=args.months,
        )
    except Exception as exc:  # CLI boundary: concise failure, non-zero exit.
        print(f"plan generation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "plan_sha256": receipt["plan_sha256"],
                "first_active_month": receipt["first_active_month"],
                "selected_day_counts": {k: len(v) for k, v in receipt["selected_days"].items()},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
