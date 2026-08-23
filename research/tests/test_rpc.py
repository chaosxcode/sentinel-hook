from __future__ import annotations

import unittest

from research.sentinel_data.rpc import JsonRpcClient, RpcError


class NoBatchClient(JsonRpcClient):
    def __init__(self) -> None:
        super().__init__("https://not-used.invalid")
        self.sequential_calls: list[int] = []

    def batch(self, calls):  # type: ignore[no-untyped-def]
        list(calls)
        raise RpcError("batch disabled")

    def call(self, method, params):  # type: ignore[no-untyped-def]
        self.assert_method(method)
        number = int(params[0], 16)
        self.sequential_calls.append(number)
        return {"number": hex(number), "hash": "0x" + f"{number:064x}", "timestamp": hex(1_700_000_000 + number)}

    @staticmethod
    def assert_method(method: str) -> None:
        if method != "eth_getBlockByNumber":
            raise AssertionError(method)


class RpcFallbackTests(unittest.TestCase):
    def test_block_lookup_falls_back_when_batch_is_disabled(self) -> None:
        client = NoBatchClient()
        receipts = client.get_blocks([3, 1, 3])
        self.assertEqual(client.sequential_calls, [1, 3])
        self.assertEqual(receipts[3].number, 3)
        self.assertEqual(receipts[1].timestamp, 1_700_000_001)


if __name__ == "__main__":
    unittest.main()
