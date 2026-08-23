"""Verify committed Sentinel extraction artifacts without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .extract import MANIFEST_SCHEMA, SCHEMA


def verify_artifacts(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    events_path = output_dir / "events.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"unsupported manifest schema {manifest.get('schema')}")

    content = events_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != manifest.get("events_sha256"):
        raise ValueError(f"events hash mismatch: expected {manifest.get('events_sha256')}, received {digest}")

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on events line {line_number}") from exc
        if record.get("schema") != SCHEMA:
            raise ValueError(f"unsupported event schema on line {line_number}")
        records.append(record)

    if len(records) != manifest.get("record_count"):
        raise ValueError("record count does not match manifest")
    counts = dict(sorted(Counter(record.get("event") for record in records).items()))
    if counts != manifest.get("event_counts"):
        raise ValueError("event counts do not match manifest")
    if records != sorted(records, key=lambda row: (row["block_number"], row["transaction_index"], row["log_index"])):
        raise ValueError("events are not in canonical chain order")
    if len({record["pool_id"] for record in records}) != manifest.get("observed_pool_count"):
        raise ValueError("observed pool count does not match manifest")
    return {"name": manifest["name"], "record_count": len(records), "events_sha256": digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        receipts = [verify_artifacts(path) for path in args.output_dirs]
    except Exception as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
