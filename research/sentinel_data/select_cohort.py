"""Deterministic Gate 1 core-cohort selection from verified event receipts.

The selection rule is frozen in ``COHORT_RULE`` below and referenced verbatim
by the Gate 1 preregistration. Currency pairs are resolved on-chain from ERC-20
Transfer logs inside sampled swap transactions because wide historical
``Initialize`` scans are not feasible on public Unichain endpoints; pools whose
pairs cannot be resolved this way are excluded rather than guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .verify import verify_artifacts


SCHEMA = "sentinel.cohort.v1"
CONFIG_SCHEMA = "sentinel.cohort-config.v1"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

COHORT_RULE: dict[str, Any] = {
    "rule_id": "sentinel-cohort-rule-v1",
    "min_swaps_in_window": 500,
    "require_resolved_currency_pair": True,
    "distinct_unordered_currency_pairs": True,
    "core_pools": 3,
    "alternate_pools": 3,
    "rank_by": "swap_count_desc_then_pool_id_asc",
    "pair_resolution_method": "erc20-transfer-intersection-in-single-pool-swap-txs",
    "max_swap_transactions_sampled_per_pool": 5,
    "min_clean_swap_transactions_for_pair_resolution": 3,
    "allow_native_eth_pairs": True,
}

ZERO_ADDRESS = "0x" + "00" * 20


def load_verified_events(evidence_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = evidence_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"{evidence_dir} has no manifest.json")
    verify_artifacts(evidence_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (evidence_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, records


def compute_pool_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    swaps: Counter[str] = Counter()
    modifies: Counter[str] = Counter()
    initializes: set[str] = set()
    for row in records:
        event = row["event"]
        if event == "Swap":
            swaps[row["pool_id"]] += 1
        elif event == "ModifyLiquidity":
            modifies[row["pool_id"]] += 1
        elif event == "Initialize":
            initializes.add(row["pool_id"])
    stats: dict[str, dict[str, int]] = {}
    for pool_id in set(swaps) | set(modifies) | initializes:
        stats[pool_id] = {
            "swap_count": swaps.get(pool_id, 0),
            "modify_liquidity_count": modifies.get(pool_id, 0),
            "initialize_observed": int(pool_id in initializes),
        }
    return stats


def rank_pools(stats: dict[str, dict[str, int]]) -> list[str]:
    return sorted(stats, key=lambda pool_id: (-stats[pool_id]["swap_count"], pool_id))


def summarize_ranked(
    ranked: list[str], stats: dict[str, dict[str, int]], limit: int = 15
) -> list[dict[str, Any]]:
    rows = []
    for position, pool_id in enumerate(ranked[:limit], start=1):
        row = {"rank": position, "pool_id": pool_id, **stats[pool_id]}
        rows.append(row)
    return rows


def resolve_pair_via_transfers(
    client: Any,
    pool_manager: str,
    pool_id: str,
    swap_keys: list[tuple[int, int, str]],
    *,
    max_samples: int,
    min_samples: int,
) -> tuple[list[str], str]:
    """Resolve a pool's currency pair from ERC-20 Transfer logs touching the PoolManager.

    Only single-pool swap transactions are sampled: router transactions that
    swap through several pools transfer unrelated tokens and would poison the
    intersection. Pools without enough clean samples are excluded rather than
    guessed.
    """

    pm = pool_manager.lower()
    common_tokens: set[str] | None = None
    sampled = 0
    for _, _, tx_hash in swap_keys:
        if sampled >= max_samples:
            break
        sampled += 1
        receipt = client.transaction_receipt(tx_hash)
        tokens: set[str] = set()
        for log in receipt.get("logs", []):
            topics = log.get("topics", [])
            if not topics or str(topics[0]).lower() != TRANSFER_TOPIC:
                continue
            if len(topics) < 3:
                continue
            sender = "0x" + str(topics[1])[-40:]
            recipient = "0x" + str(topics[2])[-40:]
            if sender != pm and recipient != pm:
                continue
            token = str(log.get("address", "")).lower()
            if token != ZERO_ADDRESS:
                tokens.add(token)
        if not tokens:
            continue
        common_tokens = tokens if common_tokens is None else (common_tokens & tokens)
        if common_tokens is not None and len(common_tokens) == 2 and sampled >= min_samples:
            return sorted(common_tokens), "resolved"
    if common_tokens is None:
        return [], "no_transfer_logs_found"
    if sampled < min_samples:
        return sorted(common_tokens), f"insufficient_clean_samples:{sampled}"
    if len(common_tokens) == 2:
        return sorted(common_tokens), "resolved"
    if len(common_tokens) == 1:
        # In v4 the only non-ERC20 currency is native ETH (address(0)). A
        # clean single-pool swap that consistently moves exactly one ERC-20
        # token therefore identifies a native-side pair.
        if COHORT_RULE["allow_native_eth_pairs"]:
            return sorted(list(common_tokens) + [ZERO_ADDRESS]), "resolved_with_native_currency"
        return sorted(common_tokens), "single_token_native_side_suspected"
    return sorted(common_tokens), f"intersection_size_{len(common_tokens)}"


def _swap_keys_for_pool(records: list[dict[str, Any]], pool_id: str) -> list[tuple[int, int, str]]:
    """Swap txs of ``pool_id`` that touch no other pool, in canonical order.

    Multi-pool router transactions are skipped because their receipts carry
    transfers from unrelated pools; the intersection method relies on clean
    samples.
    """

    pools_by_tx: dict[str, set[str]] = {}
    keys_by_tx: dict[str, tuple[int, int, str]] = {}
    for row in records:
        if row["event"] != "Swap":
            continue
        tx_hash = row["transaction_hash"]
        pools_by_tx.setdefault(tx_hash, set()).add(row["pool_id"])
        keys_by_tx[tx_hash] = (row["block_number"], row["transaction_index"], tx_hash)
    return sorted(
        keys_by_tx[tx] for tx, pool_ids in pools_by_tx.items() if pool_ids == {pool_id}
    )


def select_cohort(
    ranked: list[str],
    stats: dict[str, dict[str, int]],
    resolve_pair: Callable[[str], tuple[list[str], str]],
    rule: dict[str, Any] = COHORT_RULE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Walk the ranking once; returns (core_rows, alternate_rows, excluded_rows)."""

    core: list[dict[str, Any]] = []
    alternates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_pairs: set[frozenset[str]] = set()

    for pool_id in ranked:
        if stats[pool_id]["swap_count"] < int(rule["min_swaps_in_window"]):
            break
        row: dict[str, Any] = {"pool_id": pool_id, **stats[pool_id]}
        currencies, status = resolve_pair(pool_id)
        row["currency_pair_status"] = status
        row["currencies"] = currencies
        if status not in ("resolved", "resolved_with_native_currency"):
            row["exclusion_reason"] = f"pair_not_resolved:{status}"
            excluded.append(row)
            continue
        pair_key = frozenset(currencies)
        if rule["distinct_unordered_currency_pairs"] and pair_key in seen_pairs:
            row["exclusion_reason"] = "duplicate_currency_pair"
            excluded.append(row)
            continue
        seen_pairs.add(pair_key)
        target = core if len(core) < int(rule["core_pools"]) else alternates
        target.append(row)
        if len(core) >= int(rule["core_pools"]) and len(alternates) >= int(rule["alternate_pools"]):
            break
    return core, alternates, excluded


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_cohort_receipt(
    config: dict[str, Any],
    inputs: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]],
    resolve_pair_factory: Callable[[], Callable[[str], tuple[list[str], str]]],
    output_path: Path,
) -> dict[str, Any]:
    expected_chain_id = int(config["chain_id"])
    for _, manifest, _ in inputs:
        if int(manifest["chain_id"]) != expected_chain_id:
            raise ValueError(f"evidence chain id {manifest['chain_id']} != config chain id {expected_chain_id}")

    merged_records: list[dict[str, Any]] = []
    input_receipts = []
    for path, manifest, records in inputs:
        merged_records.extend(records)
        input_receipts.append(
            {
                "name": manifest["name"],
                "events_sha256": manifest["events_sha256"],
                "record_count": manifest["record_count"],
                "range": manifest["range"],
            }
        )

    stats = compute_pool_stats(merged_records)
    ranked = rank_pools(stats)
    resolve_pair = resolve_pair_factory()
    core, alternates, excluded = select_cohort(ranked, stats, resolve_pair)

    window = inputs[0][1]["range"]
    hashed_payload = {
        "schema": SCHEMA,
        "name": config["name"],
        "chain_id": expected_chain_id,
        "pool_manager": str(config["pool_manager"]).lower(),
        "window": window,
        "inputs": input_receipts,
        "rule": COHORT_RULE,
        "core_pool_ids": [row["pool_id"] for row in core],
        "alternate_pool_ids": [row["pool_id"] for row in alternates],
    }
    receipt = {
        **hashed_payload,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_evidence": [str(path) for path, _, _ in inputs],
        "observed_pool_count": len(stats),
        "total_swaps_in_window": sum(row["swap_count"] for row in stats.values()),
        "top_ranked_summary": summarize_ranked(ranked, stats),
        "excluded_candidates": excluded,
        "core": core,
        "alternates": alternates,
        "selection_sha256": canonical_payload_hash(hashed_payload),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    return receipt


def _load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ("name", "chain_id", "pool_manager", "evidence")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"config is missing: {', '.join(missing)}")
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"config schema must be {CONFIG_SCHEMA}")
    return config


def _rpc_url(config: dict[str, Any]) -> str:
    env_name = config.get("rpc_url_env")
    if env_name and os.environ.get(str(env_name)):
        return os.environ[str(env_name)]
    public_url = config.get("rpc_url")
    if public_url:
        return str(public_url)
    raise ValueError("config needs rpc_url or a populated rpc_url_env")


def _make_transfer_pair_resolver(
    client: Any, pool_manager: str, records: list[dict[str, Any]]
) -> Callable[[str], tuple[list[str], str]]:
    pm = str(pool_manager).lower()
    swap_cache: dict[str, list[tuple[int, int, str]]] = {}

    def resolve(pool_id: str) -> tuple[list[str], str]:
        if pool_id not in swap_cache:
            swap_cache[pool_id] = _swap_keys_for_pool(records, pool_id)
        return resolve_pair_via_transfers(
            client,
            pm,
            pool_id,
            swap_cache[pool_id],
            max_samples=int(COHORT_RULE["max_swap_transactions_sampled_per_pool"]),
            min_samples=int(COHORT_RULE["min_clean_swap_transactions_for_pair_resolution"]),
        )

    return resolve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from .rpc import JsonRpcClient

        config = _load_config(args.config)
        inputs = []
        for name in config["evidence"]:
            evidence_dir = Path(name)
            manifest, records = load_verified_events(evidence_dir)
            inputs.append((evidence_dir, manifest, records))
        client = JsonRpcClient(_rpc_url(config))
        observed_chain_id = client.chain_id()
        if observed_chain_id != int(config["chain_id"]):
            raise RuntimeError(
                f"chain id mismatch: expected {config['chain_id']}, received {observed_chain_id}"
            )
        factory = _make_transfer_pair_resolver(
            client, config["pool_manager"], [record for _, _, records in inputs for record in records]
        )
        receipt = build_cohort_receipt(
            config,
            inputs,
            lambda: factory,
            args.output,
        )
    except Exception as exc:  # CLI boundary: show a concise failure and exit non-zero.
        print(f"cohort selection failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "name": receipt["name"],
                "core_pool_ids": receipt["core_pool_ids"],
                "alternate_pool_ids": receipt["alternate_pool_ids"],
                "selection_sha256": receipt["selection_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
