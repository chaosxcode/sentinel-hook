"""Strict decoders for the PoolManager events used by Sentinel.

The extractor intentionally decodes only the canonical v4 PoolManager events
needed for the first research stage. Large integers are serialized as decimal
strings so downstream JavaScript and spreadsheet tooling cannot silently lose
precision.
"""

from __future__ import annotations

from typing import Any


EVENT_TOPICS = {
    "Initialize": "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438",
    "Swap": "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f",
    "ModifyLiquidity": "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec",
}

_EVENT_NAMES = {topic: name for name, topic in EVENT_TOPICS.items()}


class DecodeError(ValueError):
    """Raised when a log does not match the canonical event shape."""


def _hex_bytes(value: str, *, label: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise DecodeError(f"{label} must be 0x-prefixed hex")
    try:
        return bytes.fromhex(value[2:])
    except ValueError as exc:
        raise DecodeError(f"{label} contains invalid hex") from exc


def _quantity(value: str | int | None, *, label: str) -> int:
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.startswith("0x"):
        raise DecodeError(f"{label} must be a JSON-RPC quantity")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise DecodeError(f"{label} contains an invalid quantity") from exc


def _words(data: str, expected: int) -> list[bytes]:
    raw = _hex_bytes(data, label="data")
    if len(raw) != expected * 32:
        raise DecodeError(f"event data has {len(raw)} bytes; expected {expected * 32}")
    return [raw[index : index + 32] for index in range(0, len(raw), 32)]


def _topic(topics: list[str], index: int, *, label: str) -> bytes:
    try:
        raw = _hex_bytes(topics[index], label=label)
    except IndexError as exc:
        raise DecodeError(f"missing {label}") from exc
    if len(raw) != 32:
        raise DecodeError(f"{label} must be 32 bytes")
    return raw


def _address(word: bytes) -> str:
    if len(word) != 32:
        raise DecodeError("address word must be 32 bytes")
    if any(word[:12]):
        raise DecodeError("address word has non-zero padding")
    return "0x" + word[12:].hex()


def _uint(word: bytes, bits: int) -> int:
    value = int.from_bytes(word, "big")
    if value >= 1 << bits:
        raise DecodeError(f"uint{bits} value has non-zero upper bits")
    return value


def _int(word: bytes, bits: int) -> int:
    raw = int.from_bytes(word, "big")
    mask = (1 << bits) - 1
    value = raw & mask
    if value & (1 << (bits - 1)):
        value -= 1 << bits

    expected_padding = mask if value < 0 else 0
    if raw >> bits != expected_padding >> bits:
        # expected_padding above is deliberately width-limited; compare the
        # actual ABI sign extension explicitly instead.
        upper = raw >> bits
        expected_upper = (1 << (256 - bits)) - 1 if value < 0 else 0
        if upper != expected_upper:
            raise DecodeError(f"int{bits} value has invalid sign extension")
    return value


def _common(log: dict[str, Any]) -> dict[str, Any]:
    return {
        "block_number": _quantity(log.get("blockNumber"), label="blockNumber"),
        "block_hash": str(log.get("blockHash", "")).lower(),
        "transaction_hash": str(log.get("transactionHash", "")).lower(),
        "transaction_index": _quantity(log.get("transactionIndex"), label="transactionIndex"),
        "log_index": _quantity(log.get("logIndex"), label="logIndex"),
        "removed": bool(log.get("removed", False)),
    }


def decode_log(log: dict[str, Any]) -> dict[str, Any]:
    """Decode one canonical PoolManager log into a normalized record."""

    topics = log.get("topics")
    if not isinstance(topics, list) or not topics:
        raise DecodeError("log must contain topics")

    signature = str(topics[0]).lower()
    event_name = _EVENT_NAMES.get(signature)
    if event_name is None:
        raise DecodeError(f"unsupported event topic {signature}")

    record = _common(log)
    record["event"] = event_name
    record["pool_id"] = "0x" + _topic(topics, 1, label="pool id").hex()

    if event_name == "Initialize":
        if len(topics) != 4:
            raise DecodeError("Initialize must have four topics")
        words = _words(log.get("data", ""), 5)
        record.update(
            {
                "currency0": _address(_topic(topics, 2, label="currency0")),
                "currency1": _address(_topic(topics, 3, label="currency1")),
                "fee": _uint(words[0], 24),
                "tick_spacing": _int(words[1], 24),
                "hooks": _address(words[2]),
                "sqrt_price_x96": str(_uint(words[3], 160)),
                "tick": _int(words[4], 24),
            }
        )
        return record

    if len(topics) != 3:
        raise DecodeError(f"{event_name} must have three topics")
    record["sender"] = _address(_topic(topics, 2, label="sender"))

    if event_name == "Swap":
        words = _words(log.get("data", ""), 6)
        record.update(
            {
                "amount0": str(_int(words[0], 128)),
                "amount1": str(_int(words[1], 128)),
                "sqrt_price_x96": str(_uint(words[2], 160)),
                "liquidity": str(_uint(words[3], 128)),
                "tick": _int(words[4], 24),
                "fee": _uint(words[5], 24),
            }
        )
        return record

    words = _words(log.get("data", ""), 4)
    record.update(
        {
            "tick_lower": _int(words[0], 24),
            "tick_upper": _int(words[1], 24),
            "liquidity_delta": str(_int(words[2], 256)),
            "salt": "0x" + words[3].hex(),
        }
    )
    return record
