"""Reference-price venue discovery for the frozen Gate 1 cohort.

Consumes the nomination receipt and resolves currency pairs for the top-ranked
pools using the same frozen transfer-intersection method as cohort selection,
then applies the preregistration's venue-of-record rule: for each study pool,
every other pool trading its unordered currency pair, plus the canonical deep
pools for the quote assets (native ETH/USDC and native ETH/USDt0). The result
is a hash-receipted venue set frozen before any label computation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .rpc import JsonRpcClient
from .select_cohort import (
    ZERO_ADDRESS,
    _make_transfer_pair_resolver,
    load_verified_events,
)


SCHEMA = "sentinel.venues.v1"

STUDY_PAIRS = {
    "core1_usdc_sol": frozenset(
        {"0x078d782b760474a361dda0af3839290b0ef57ad6", "0xbde8a5331e8ac4831cf8ea9e42e229219eafab97"}
    ),
    "core2_usdc_hype": frozenset(
        {"0x078d782b760474a361dda0af3839290b0ef57ad6", "0x15d0e0c55a3e7ee67152ad7e89acf164253ff68d"}
    ),
    "core3_eth_usdc": frozenset(
        {ZERO_ADDRESS, "0x078d782b760474a361dda0af3839290b0ef57ad6"}
    ),
}

QUOTE_DEEP_PAIRS = {
    "quote_eth_usdc": frozenset({ZERO_ADDRESS, "0x078d782b760474a361dda0af3839290b0ef57ad6"}),
    "quote_eth_usdt0": frozenset({ZERO_ADDRESS, "0x9151434b16b9763660705744891fa906f660ecc5"}),
}


def select_venues(
    ranked_pool_ids: list[str],
    stats: dict[str, dict[str, int]],
    resolve_pair: Any,
    *,
    study_pairs: dict[str, frozenset[str]] | None = None,
    quote_deep_pairs: dict[str, frozenset[str]] | None = None,
    max_candidates: int = 40,
    min_swaps_for_venue: int = 100,
    probe_all_ranked: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Resolve pairs for the top ``max_candidates`` pools and assign venues."""

    study_pairs = study_pairs if study_pairs is not None else STUDY_PAIRS
    quote_deep_pairs = quote_deep_pairs if quote_deep_pairs is not None else QUOTE_DEEP_PAIRS

    resolved: dict[str, tuple[list[str], str]] = {}
    probed = 0
    for pool_id in ranked_pool_ids:
        if not probe_all_ranked and probed >= max_candidates:
            break
        threshold = 0 if probe_all_ranked else min_swaps_for_venue
        if stats[pool_id]["swap_count"] < threshold:
            break
        resolved[pool_id] = resolve_pair(pool_id)
        probed += 1

    venues: dict[str, list[dict[str, Any]]] = {}
    assigned: set[str] = set()
    for role, pair_key in study_pairs.items():
        rows = []
        for pool_id, (currencies, status) in resolved.items():
            if status not in ("resolved", "resolved_with_native_currency"):
                continue
            if frozenset(currencies) != pair_key:
                continue
            rows.append(
                {
                    "pool_id": pool_id,
                    "currencies": sorted(currencies),
                    "pair_status": status,
                    "swap_count_in_window": stats[pool_id]["swap_count"],
                }
            )
        venues[role] = rows
        for row in rows:
            assigned.add(row["pool_id"])

    quote_rows = []
    for role, pair_key in quote_deep_pairs.items():
        for pool_id, (currencies, status) in resolved.items():
            if pool_id in assigned:
                continue
            if status not in ("resolved", "resolved_with_native_currency"):
                continue
            if frozenset(currencies) != pair_key:
                continue
            quote_rows.append(
                {
                    "role": role,
                    "pool_id": pool_id,
                    "currencies": sorted(currencies),
                    "pair_status": status,
                    "swap_count_in_window": stats[pool_id]["swap_count"],
                }
            )
    return venues, quote_rows


def build_venue_receipt(
    config: dict[str, Any],
    nomination_dir: Path,
    output_path: Path,
    client_factory: Any,
    *,
    max_candidates: int = 40,
    min_swaps_for_venue: int = 100,
    probe_all_ranked: bool = False,
) -> dict[str, Any]:
    manifest, records = load_verified_events(nomination_dir)

    stats: dict[str, dict[str, int]] = {}
    from .select_cohort import compute_pool_stats, rank_pools

    stats = compute_pool_stats(records)
    ranked = rank_pools(stats)

    client = client_factory()
    observed_chain_id = client.chain_id()
    if observed_chain_id != int(config["chain_id"]):
        raise RuntimeError(
            f"chain id mismatch: expected {config['chain_id']}, received {observed_chain_id}"
        )
    resolve_pair = _make_transfer_pair_resolver(client, config["pool_manager"], records)
    venues, quote_rows = select_venues(
        ranked,
        stats,
        resolve_pair,
        max_candidates=max_candidates,
        min_swaps_for_venue=min_swaps_for_venue,
        probe_all_ranked=probe_all_ranked,
    )

    hashed_payload = {
        "schema": SCHEMA,
        "name": config["name"] + "-venues",
        "chain_id": int(config["chain_id"]),
        "inputs": [
            {
                "name": manifest["name"],
                "events_sha256": manifest["events_sha256"],
                "record_count": manifest["record_count"],
            }
        ],
        "study_pair_keys": sorted(sorted(tokens) for tokens in STUDY_PAIRS.values()),
        "venues": venues,
        "quote_deep_venues": quote_rows,
    }
    receipt = {
        **hashed_payload,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_evidence": str(nomination_dir),
        "venues_sha256": hashlib.sha256(
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
    parser.add_argument("--nomination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument("--min-swaps-for-venue", type=int, default=100)
    parser.add_argument("--probe-all-ranked", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        env_name = config.get("rpc_url_env")
        url = (
            os.environ[str(env_name)]
            if env_name and os.environ.get(str(env_name))
            else str(config["rpc_url"])
        )

        def factory() -> JsonRpcClient:
            return JsonRpcClient(url)

        receipt = build_venue_receipt(
            config,
            args.nomination,
            args.output,
            factory,
            max_candidates=args.max_candidates,
            min_swaps_for_venue=args.min_swaps_for_venue,
            probe_all_ranked=args.probe_all_ranked,
        )
    except Exception as exc:  # CLI boundary: show a concise failure and exit non-zero.
        print(f"venue discovery failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"venues_sha256": receipt["venues_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
