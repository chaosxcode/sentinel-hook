"""Small dependency-free JSON-RPC client with adaptive log chunking."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable


class RpcError(RuntimeError):
    """Raised when a JSON-RPC request cannot be completed safely."""


class RateLimited(RpcError):
    """Raised on 429/403 so callers can rotate endpoints instead of halving."""


@dataclass(frozen=True)
class BlockReceipt:
    number: int
    hash: str
    timestamp: int


class JsonRpcClient:
    def __init__(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        retries: int = 8,
        pace: float = 0.0,
        retry_on_rate_limit: bool = False,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._retries = retries
        self._pace = pace
        self._request_id = 0
        self._id_lock = threading.Lock()
        self._retry_on_rate_limit = retry_on_rate_limit

    def _post(self, payload: Any) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self._retries):
            request = urllib.request.Request(
                self._url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "sentinel-data-pipeline/0.1"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    result = json.load(response)
                if self._pace:
                    time.sleep(self._pace)
                return result
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 403):
                    if self._retry_on_rate_limit and attempt + 1 < self._retries:
                        time.sleep(min(30.0, 2.5 * (2**attempt)))
                        continue
                    raise RateLimited(f"RPC {exc.code}: {exc.reason}") from exc
                if exc.code == 400:
                    raise
                last_error = exc
                if attempt + 1 < self._retries:
                    time.sleep(min(20.0, 1.0 * (2**attempt)))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self._retries:
                    time.sleep(min(20.0, 1.0 * (2**attempt)))
        raise RpcError(f"RPC transport failed after {self._retries} attempts: {last_error}")

    def call(self, method: str, params: list[Any]) -> Any:
        with self._id_lock:
            self._request_id += 1
            request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        response = self._post(payload)
        if not isinstance(response, dict):
            raise RpcError(f"RPC {method} returned a non-object response")
        if "error" in response:
            error = response["error"]
            raise RpcError(f"RPC {method} error {error}")
        if "result" not in response:
            raise RpcError(f"RPC {method} response has no result")
        return response["result"]

    def batch(self, calls: Iterable[tuple[str, list[Any]]]) -> list[Any]:
        payload: list[dict[str, Any]] = []
        order: list[int] = []
        for method, params in calls:
            with self._id_lock:
                self._request_id += 1
                request_id = self._request_id
            order.append(request_id)
            payload.append({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if not payload:
            return []

        response = self._post(payload)
        if not isinstance(response, list):
            raise RpcError("RPC batch returned a non-array response")
        by_id = {item.get("id"): item for item in response if isinstance(item, dict)}
        results: list[Any] = []
        for request_id in order:
            item = by_id.get(request_id)
            if item is None:
                raise RpcError(f"RPC batch omitted response id {request_id}")
            if "error" in item:
                raise RpcError(f"RPC batch request {request_id} error {item['error']}")
            results.append(item.get("result"))
        return results

    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def transaction_receipt(self, tx_hash: str) -> dict[str, Any]:
        receipt = self.call("eth_getTransactionReceipt", [tx_hash])
        if not isinstance(receipt, dict) or not receipt:
            raise RpcError(f"transaction {tx_hash} has no receipt")
        return receipt

    def call_contract(
        self,
        *,
        to: str,
        data: str,
        block: str,
    ) -> str | None:
        """eth_call against a pinned block identifier (hash or quantity string)."""

        result = self.call("eth_call", [{"to": to, "data": data}, block])
        if result is None:
            return None
        if not isinstance(result, str) or not result.startswith("0x"):
            raise RpcError(f"eth_call returned a non-hex result for {to}")
        return result

    def get_logs(
        self,
        *,
        address: str,
        topics: list[Any],
        from_block: int,
        to_block: int,
        chunk_size: int,
    ) -> list[dict[str, Any]]:
        if from_block > to_block:
            raise ValueError("from_block must not exceed to_block")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")

        logs: list[dict[str, Any]] = []
        cursor = from_block
        active_chunk = chunk_size
        while cursor <= to_block:
            end = min(cursor + active_chunk - 1, to_block)
            params = [
                {
                    "address": address,
                    "fromBlock": hex(cursor),
                    "toBlock": hex(end),
                    "topics": topics,
                }
            ]
            try:
                result = self.call("eth_getLogs", params)
            except RateLimited:
                raise
            except RpcError:
                if active_chunk == 1:
                    raise
                active_chunk = max(1, active_chunk // 2)
                continue
            if not isinstance(result, list):
                raise RpcError("eth_getLogs returned a non-array result")
            logs.extend(result)
            cursor = end + 1
            active_chunk = min(chunk_size, active_chunk * 2)
        return logs

    def get_blocks(
        self,
        numbers: Iterable[int],
        *,
        batch_size: int = 100,
        workers: int = 6,
    ) -> dict[int, BlockReceipt]:
        unique = sorted(set(numbers))
        receipts: dict[int, BlockReceipt] = {}
        chunks = [unique[offset : offset + batch_size] for offset in range(0, len(unique), batch_size)]

        def fetch_chunk(chunk: list[int]) -> list[Any]:
            try:
                return self.batch(("eth_getBlockByNumber", [hex(number), False]) for number in chunk)
            except RateLimited:
                raise
            except RpcError:
                if len(chunk) == 1:
                    raise
                # Some otherwise standards-compliant public endpoints disable
                # JSON-RPC batching. Preserve correctness with a slower
                # sequential fallback instead of requiring a specific vendor.
                return [self.call("eth_getBlockByNumber", [hex(number), False]) for number in chunk]

        chunk_results: list[list[Any]] = []
        if len(chunks) > 1 and workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                chunk_results = list(executor.map(fetch_chunk, chunks))
        else:
            chunk_results = [fetch_chunk(chunk) for chunk in chunks]
        for chunk, results in zip(chunks, chunk_results, strict=True):
            for requested, block in zip(chunk, results, strict=True):
                if not isinstance(block, dict):
                    raise RpcError(f"block {requested} was not returned")
                actual = int(block["number"], 16)
                if actual != requested:
                    raise RpcError(f"requested block {requested}; received {actual}")
                receipts[requested] = BlockReceipt(
                    number=actual,
                    hash=str(block["hash"]).lower(),
                    timestamp=int(block["timestamp"], 16),
                )
        return receipts
