"""CLI for deterministic raw Uniswap v4 event extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .events import EVENT_TOPICS, decode_log
from .rpc import JsonRpcClient


SCHEMA = "sentinel.v4-events.v1"
MANIFEST_SCHEMA = "sentinel.v4-extraction-manifest.v1"


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = ("name", "chain_id", "pool_manager", "from_block", "to_block")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"config is missing: {', '.join(missing)}")
    if int(config["from_block"]) > int(config["to_block"]):
        raise ValueError("config from_block must not exceed to_block")
    return config


def _rpc_url(config: dict[str, Any]) -> tuple[str, str]:
    env_name = config.get("rpc_url_env")
    if env_name and os.environ.get(str(env_name)):
        return os.environ[str(env_name)], f"env:{env_name}"
    public_url = config.get("rpc_url")
    if public_url:
        return str(public_url), "config:public_rpc_url"
    raise ValueError("config needs rpc_url or a populated rpc_url_env")


def _canonical_line(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def extract(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = _load_config(config_path)
    rpc_url, rpc_source = _rpc_url(config)
    client = JsonRpcClient(rpc_url)

    expected_chain_id = int(config["chain_id"])
    observed_chain_id = client.chain_id()
    if observed_chain_id != expected_chain_id:
        raise RuntimeError(f"chain id mismatch: expected {expected_chain_id}, received {observed_chain_id}")

    selected_events = config.get("events", list(EVENT_TOPICS))
    unknown_events = sorted(set(selected_events) - set(EVENT_TOPICS))
    if unknown_events:
        raise ValueError(f"unsupported config events: {', '.join(unknown_events)}")
    event_topics = [EVENT_TOPICS[name] for name in selected_events]
    topics: list[Any] = [event_topics]

    pool_ids = [str(value).lower() for value in config.get("pool_ids", [])]
    if pool_ids:
        for pool_id in pool_ids:
            if not pool_id.startswith("0x") or len(pool_id) != 66:
                raise ValueError(f"invalid pool id {pool_id}")
        topics.append(pool_ids)

    from_block = int(config["from_block"])
    to_block = int(config["to_block"])
    logs = client.get_logs(
        address=str(config["pool_manager"]),
        topics=topics,
        from_block=from_block,
        to_block=to_block,
        chunk_size=int(config.get("chunk_size", 2_000)),
    )

    records = [decode_log(log) for log in logs if not log.get("removed", False)]
    records.sort(key=lambda row: (row["block_number"], row["transaction_index"], row["log_index"]))

    identities = [(row["block_hash"], row["transaction_hash"], row["log_index"]) for row in records]
    if len(identities) != len(set(identities)):
        raise RuntimeError("RPC returned duplicate logs")

    receipt_numbers = {from_block, to_block, *(row["block_number"] for row in records)}
    blocks = client.get_blocks(receipt_numbers)
    for record in records:
        receipt = blocks[record["block_number"]]
        if record["block_hash"] != receipt.hash:
            raise RuntimeError(f"block hash mismatch at {receipt.number}; refusing reorg-ambiguous output")
        record["schema"] = SCHEMA
        record["chain_id"] = observed_chain_id
        record["block_timestamp"] = receipt.timestamp
        record["block_timestamp_iso"] = datetime.fromtimestamp(receipt.timestamp, UTC).isoformat()

    events_content = b"".join(_canonical_line(record) for record in records)
    events_hash = hashlib.sha256(events_content).hexdigest()
    event_counts = Counter(row["event"] for row in records)
    observed_pools = sorted({row["pool_id"] for row in records})

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "config": str(config_path),
        "name": config["name"],
        "chain_id": observed_chain_id,
        "pool_manager": str(config["pool_manager"]).lower(),
        "rpc_source": rpc_source,
        "range": {
            "from_block": from_block,
            "from_block_hash": blocks[from_block].hash,
            "from_block_timestamp": blocks[from_block].timestamp,
            "to_block": to_block,
            "to_block_hash": blocks[to_block].hash,
            "to_block_timestamp": blocks[to_block].timestamp,
        },
        "selected_events": selected_events,
        "selected_pool_ids": pool_ids,
        "record_count": len(records),
        "event_counts": dict(sorted(event_counts.items())),
        "observed_pool_count": len(observed_pools),
        "observed_pool_ids": observed_pools,
        "events_file": "events.jsonl",
        "events_sha256": events_hash,
        "generated_at": datetime.now(UTC).isoformat(),
        "limitations": [
            "Raw PoolManager events are ingestion evidence, not a Gate 1 result.",
            "Token metadata, reference prices, LVR labels, and pool-selection rules are not inferred here.",
            "Hooks with swap-return deltas require hook-aware accounting beyond the default Swap event.",
        ],
    }

    _write_atomic(output_dir / "events.jsonl", events_content)
    manifest_content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(output_dir / "manifest.json", manifest_content)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = extract(args.config, args.output_dir)
    except Exception as exc:  # CLI boundary: show a concise failure and exit non-zero.
        print(f"extraction failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({key: manifest[key] for key in ("name", "record_count", "event_counts", "events_sha256")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
