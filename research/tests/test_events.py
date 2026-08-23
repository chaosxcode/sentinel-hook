from __future__ import annotations

import unittest

from research.sentinel_data.events import EVENT_TOPICS, DecodeError, decode_log


def _word(value: int, *, signed_bits: int | None = None) -> str:
    if signed_bits is not None and value < 0:
        value = (1 << 256) + value
    return value.to_bytes(32, "big").hex()


def _address(value: str) -> str:
    return (b"\x00" * 12 + bytes.fromhex(value[2:])).hex()


def _base_log(topic: str) -> dict[str, object]:
    return {
        "blockNumber": "0x64",
        "blockHash": "0x" + "ab" * 32,
        "transactionHash": "0x" + "cd" * 32,
        "transactionIndex": "0x2",
        "logIndex": "0x3",
        "removed": False,
        "topics": [topic, "0x" + "11" * 32],
        "data": "0x",
    }


class EventDecoderTests(unittest.TestCase):
    def test_decodes_swap_signed_values_and_exact_large_integers(self) -> None:
        log = _base_log(EVENT_TOPICS["Swap"])
        log["topics"] = [
            EVENT_TOPICS["Swap"],
            "0x" + "11" * 32,
            "0x" + _address("0x" + "22" * 20),
        ]
        log["data"] = "0x" + "".join(
            [
                _word(-123, signed_bits=128),
                _word(456, signed_bits=128),
                _word(2**159),
                _word(2**127),
                _word(-55, signed_bits=24),
                _word(3_000),
            ]
        )

        record = decode_log(log)

        self.assertEqual(record["event"], "Swap")
        self.assertEqual(record["sender"], "0x" + "22" * 20)
        self.assertEqual(record["amount0"], "-123")
        self.assertEqual(record["amount1"], "456")
        self.assertEqual(record["sqrt_price_x96"], str(2**159))
        self.assertEqual(record["liquidity"], str(2**127))
        self.assertEqual(record["tick"], -55)
        self.assertEqual(record["fee"], 3_000)

    def test_decodes_initialize(self) -> None:
        log = _base_log(EVENT_TOPICS["Initialize"])
        log["topics"] = [
            EVENT_TOPICS["Initialize"],
            "0x" + "11" * 32,
            "0x" + _address("0x" + "33" * 20),
            "0x" + _address("0x" + "44" * 20),
        ]
        log["data"] = "0x" + "".join(
            [
                _word(1 << 23),
                _word(60, signed_bits=24),
                _address("0x" + "55" * 20),
                _word(2**96),
                _word(-1, signed_bits=24),
            ]
        )

        record = decode_log(log)

        self.assertEqual(record["currency0"], "0x" + "33" * 20)
        self.assertEqual(record["currency1"], "0x" + "44" * 20)
        self.assertEqual(record["hooks"], "0x" + "55" * 20)
        self.assertEqual(record["fee"], 1 << 23)
        self.assertEqual(record["tick_spacing"], 60)
        self.assertEqual(record["tick"], -1)

    def test_rejects_unknown_event(self) -> None:
        log = _base_log("0x" + "99" * 32)
        with self.assertRaises(DecodeError):
            decode_log(log)


if __name__ == "__main__":
    unittest.main()
