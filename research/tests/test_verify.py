from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from research.sentinel_data.extract import MANIFEST_SCHEMA, SCHEMA
from research.sentinel_data.verify import verify_artifacts


class ArtifactVerificationTests(unittest.TestCase):
    def test_verifies_hash_counts_and_order(self) -> None:
        record = {
            "schema": SCHEMA,
            "event": "Swap",
            "pool_id": "0x" + "11" * 32,
            "block_number": 1,
            "transaction_index": 0,
            "log_index": 0,
        }
        content = (json.dumps(record) + "\n").encode()
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "name": "fixture",
            "record_count": 1,
            "event_counts": {"Swap": 1},
            "observed_pool_count": 1,
            "events_sha256": hashlib.sha256(content).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "events.jsonl").write_bytes(content)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            receipt = verify_artifacts(root)
        self.assertEqual(receipt["record_count"], 1)

    def test_rejects_tampered_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "events.jsonl").write_text("{}\n", encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": MANIFEST_SCHEMA,
                        "name": "fixture",
                        "record_count": 1,
                        "event_counts": {},
                        "observed_pool_count": 0,
                        "events_sha256": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                verify_artifacts(root)


if __name__ == "__main__":
    unittest.main()
