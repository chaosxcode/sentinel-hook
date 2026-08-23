"""Block-pinned token metadata (symbol, decimals, name) for cohort currencies.

Every probe is an ``eth_call`` executed against the window's boundary block
hash, so the receipt records exactly which canonical state the values were read
from. Tokens whose calls revert are recorded as non-standard rather than
guessed; unit normalization for such tokens is handled by the substitution rule
in the Gate 1 preregistration.
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


SCHEMA = "sentinel.token-metadata.v1"

ZERO_ADDRESS = "0x" + "00" * 20

SELECTORS = {
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
    "name": "0x06fdde03",
}


def _decode_string(result: str | None) -> str | None:
    if result is None:
        return None
    raw = bytes.fromhex(result[2:])
    if len(raw) < 64:
        return None
    offset = int.from_bytes(raw[0:32], "big")
    if offset + 32 > len(raw):
        return None
    length = int.from_bytes(raw[offset : offset + 32], "big")
    start = offset + 32
    if start + length > len(raw):
        return None
    return raw[start : start + length].decode("utf-8", errors="replace")


def _decode_uint8(result: str | None) -> int | None:
    if result is None or len(result) < 2 + 64:
        return None
    value = int.from_bytes(bytes.fromhex(result[2:])[:32], "big")
    if value >= 1 << 8:
        return None
    return value


def probe_token(client: JsonRpcClient, address: str, block_hash: str) -> dict[str, Any]:
    lowered = address.lower()
    row: dict[str, Any] = {"address": lowered}
    try:
        symbol_raw = client.call_contract(to=lowered, data=SELECTORS["symbol"], block=block_hash)
        decimals_raw = client.call_contract(to=lowered, data=SELECTORS["decimals"], block=block_hash)
        name_raw = client.call_contract(to=lowered, data=SELECTORS["name"], block=block_hash)
    except Exception as exc:  # noqa: BLE001 - a single broken token must not abort the receipt.
        row.update({"status": "call_failed", "error": str(exc)})
        return row
    symbol = _decode_string(symbol_raw)
    name = _decode_string(name_raw)
    decimals = _decode_uint8(decimals_raw)
    if symbol is None or decimals is None:
        row.update(
            {
                "status": "nonstandard",
                "symbol_raw": symbol_raw,
                "name_raw": name_raw,
                "decimals_raw": decimals_raw,
            }
        )
        return row
    row.update(
        {
            "status": "ok",
            "symbol": symbol,
            "name": name,
            "decimals": decimals,
            "symbol_raw": symbol_raw,
            "name_raw": name_raw,
            "decimals_raw": decimals_raw,
        }
    )
    return row


def build_metadata_receipt(
    client: JsonRpcClient,
    cohort_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    if cohort.get("schema") != "sentinel.cohort.v1":
        raise ValueError(f"{cohort_path} is not a {SCHEMA.replace('token-metadata', 'cohort')} receipt")
    block_hash = str(cohort["window"]["to_block_hash"])
    currencies = sorted(
        {
            currency.lower()
            for role in ("core", "alternates")
            for pool in cohort[role]
            for currency in pool["currencies"]
        }
    )
    probed = []
    for address in currencies:
        if address == ZERO_ADDRESS:
            probed.append(
                {
                    "address": address,
                    "status": "native_currency",
                    "symbol": "ETH",
                    "decimals": 18,
                }
            )
            continue
        probed.append(probe_token(client, address, block_hash))
    hashed_payload = {
        "schema": SCHEMA,
        "cohort_selection_sha256": cohort["selection_sha256"],
        "probed_at_block_hash": block_hash,
        "currencies": [
            {key: row[key] for key in sorted(row) if key != "error"} for row in probed
        ],
    }
    receipt = {
        **hashed_payload,
        "generated_at": datetime.now(UTC).isoformat(),
        "method_ids": SELECTORS,
        "probe_results": probed,
        "metadata_sha256": hashlib.sha256(
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
    parser.add_argument("--config", type=Path, required=True, help="same chain config used for selection")
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        env_name = config.get("rpc_url_env")
        url = (
            os.environ[str(env_name)]
            if env_name and os.environ.get(str(env_name))
            else str(config["rpc_url"])
        )
        client = JsonRpcClient(url)
        observed_chain_id = client.chain_id()
        if observed_chain_id != int(config["chain_id"]):
            raise RuntimeError(
                f"chain id mismatch: expected {config['chain_id']}, received {observed_chain_id}"
            )
        receipt = build_metadata_receipt(client, args.cohort, args.output)
    except Exception as exc:  # CLI boundary: show a concise failure and exit non-zero.
        print(f"token metadata failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "currencies": len(receipt["currencies"]),
                "statuses": sorted({row["status"] for row in receipt["probe_results"]}),
                "metadata_sha256": receipt["metadata_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
