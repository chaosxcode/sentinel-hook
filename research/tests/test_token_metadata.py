from __future__ import annotations

import unittest

from research.sentinel_data.rpc import JsonRpcClient
from research.sentinel_data.token_metadata import (
    SELECTORS,
    _decode_string,
    _decode_uint8,
    build_metadata_receipt,
)


class DecodeTests(unittest.TestCase):
    def test_decode_string(self) -> None:
        word = b"USDC".ljust(32, b"\x00")
        encoded = (
            "0x"
            + (32).to_bytes(32, "big").hex()
            + (4).to_bytes(32, "big").hex()
            + word.hex()
        )
        self.assertEqual(_decode_string(encoded), "USDC")

    def test_decode_uint8(self) -> None:
        self.assertEqual(_decode_uint8("0x" + (6).to_bytes(32, "big").hex()), 6)
        self.assertIsNone(_decode_uint8("0x" + (300).to_bytes(32, "big").hex()))
        self.assertIsNone(_decode_uint8(None))


class FakeClient(JsonRpcClient):
    def __init__(self) -> None:  # noqa: D107 - unittest fixture, bypasses network init.
        pass

    def chain_id(self) -> int:
        return 130

    def call_contract(self, *, to: str, data: str, block: str) -> str | None:
        table = {
            "0x" + "a1" * 20: {
                SELECTORS["symbol"]: _encode_string("TKA"),
                SELECTORS["decimals"]: _encode_uint8(18),
                SELECTORS["name"]: _encode_string("Token A"),
            },
            "0x" + "b1" * 20: {
                SELECTORS["symbol"]: _encode_string("TKB"),
                SELECTORS["decimals"]: _encode_uint8(6),
                SELECTORS["name"]: _encode_string("Token B"),
            },
        }
        selector = data[:10]
        if to not in table or selector not in table[to]:
            return None
        return table[to][selector]


def _encode_string(value: str) -> str:
    raw = value.encode()
    padded = raw + b"\x00" * ((32 - len(raw) % 32) % 32)
    return (
        "0x"
        + (32).to_bytes(32, "big").hex()
        + len(raw).to_bytes(32, "big").hex()
        + padded.hex()
    )


def _encode_uint8(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


class BuildMetadataReceiptTests(unittest.TestCase):
    def test_builds_receipt_for_core_and_alternates(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        cohort = {
            "schema": "sentinel.cohort.v1",
            "selection_sha256": "0" * 64,
            "window": {"to_block_hash": "0x" + "11" * 32},
            "core": [{"currencies": ["0x" + "a1" * 20, "0x" + "b1" * 20]}],
            "alternates": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cohort_path = root / "cohort.json"
            cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
            receipt = build_metadata_receipt(FakeClient(), cohort_path, root / "tokens.json")
            written = json.loads((root / "tokens.json").read_text(encoding="utf-8"))
        by_address = {row["address"]: row for row in receipt["probe_results"]}
        self.assertEqual(by_address["0x" + "a1" * 20]["symbol"], "TKA")
        self.assertEqual(by_address["0x" + "a1" * 20]["decimals"], 18)
        self.assertEqual(by_address["0x" + "b1" * 20]["decimals"], 6)
        self.assertEqual(receipt["metadata_sha256"], written["metadata_sha256"])
        self.assertEqual(receipt["probed_at_block_hash"], "0x" + "11" * 32)


if __name__ == "__main__":
    unittest.main()
