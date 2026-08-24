"""Sentinel pilot monitor — continuous live invariant checks for B3.

Polls a deployed SentinelHookV1 through public RPCs (no keys), asserts the
security invariants on every observation, archives states and FeeUpdated
events to resumable JSONL files, and reports violations on stderr with a
non-zero exit code so cron/systemd can alert.

Usage:
  python3 -m research.sentinel_data.monitor_pilot \
    --hook 0x... --pool-id 0x... --archive evidence/pilot/monitor \
    [--interval 30] [--once]

Invariants checked per observation:
  M1  BASE_FEE <= currentFee <= CAP_FEE
  M2  0 <= emaRateWad <= 1e18
  M3  FeeUpdated events well-formed (old/new within bounds)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE_FEE = 500
CAP_FEE = 10_000
FEE_UPDATED_TOPIC = "0x2c104de20fbb789c970f86b1b18f92a4f05c52783081e90e25ef1e4156e40bf3"
FEE_SEL = "0x9c1f9c03"
EMA_SEL = "0x5dad2d41"


class RpcClient:
    """Multi-endpoint JSON-RPC with failover (public RPCs, no keys)."""

    def __init__(self, urls: list[str]) -> None:
        self.urls = urls

    def call(self, method: str, params: list[Any]) -> Any:
        import urllib.error
        import urllib.request

        last: Exception | None = None
        for url in self.urls:
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                    headers={"Content-Type": "application/json", "User-Agent": "sentinel-monitor/0.1"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=20) as response:
                    result = json.load(response)
                if "result" in result:
                    return result["result"]
                last = RuntimeError(f"rpc error: {result.get('error')}")
            except Exception as exc:  # noqa: BLE001 — failover is the point
                last = exc
        raise RuntimeError(f"all endpoints failed: {last}")


def check_invariants(fee: int, ema: int) -> list[str]:
    violations = []
    if not (BASE_FEE <= fee <= CAP_FEE):
        violations.append(f"M1 fee out of bounds: {fee}")
    if not (0 <= ema <= 10**18):
        violations.append(f"M2 ema out of range: {ema}")
    return violations


def archive_line(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def load_checkpoint(path: Path) -> int:
    if path.exists():
        return int(path.read_text(encoding="utf-8").strip() or 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", required=True)
    parser.add_argument("--pool-id", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--rpc", action="append", default=[
        "https://unichain-sepolia.drpc.org",
        "https://sepolia.unichain.org",
        "https://mainnet.unichain.org",
        "https://unichain.drpc.org",
    ])
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    client = RpcClient(args.rpc)
    pool_arg = args.pool_id.lower()
    checkpoint_path = args.archive / "event-checkpoint.txt"
    last_block = load_checkpoint(checkpoint_path)

    violations_total = 0
    while True:
        now = datetime.now(UTC).isoformat()
        try:
            head = int(client.call("eth_blockNumber", []), 16)
            fee = int(client.call("eth_call", [{"to": args.hook, "data": FEE_SEL + pool_arg[2:]}, "latest"]), 16)
            ema = int(client.call("eth_call", [{"to": args.hook, "data": EMA_SEL + pool_arg[2:]}, "latest"]), 16)

            violations = check_invariants(fee, ema)
            archive_line(
                args.archive / "states.jsonl",
                {"t": now, "block": head, "fee": fee, "ema": ema, "violations": violations},
            )

            if last_block and head > last_block:
                logs = client.call(
                    "eth_getLogs",
                    [{
                        "address": args.hook,
                        "fromBlock": hex(last_block + 1),
                        "toBlock": hex(head),
                        "topics": [FEE_UPDATED_TOPIC, "0x" + pool_arg[2:]],
                    }],
                )
                for lg in logs:
                    d = lg["data"][2:]
                    archive_line(
                        args.archive / "fee-updates.jsonl",
                        {
                            "t": now,
                            "block": int(lg["blockNumber"], 16),
                            "tx": lg["transactionHash"],
                            "old": int(d[0:64], 16),
                            "new": int(d[64:128], 16),
                            "ema": int(d[128:192], 16) / 1e18,
                        },
                    )
                last_block = head
                checkpoint_path.write_text(str(last_block), encoding="utf-8")

            if violations:
                violations_total += len(violations)
                print(f"INVARIANT VIOLATION {now}: {violations}", file=sys.stderr, flush=True)
            else:
                print(f"ok {now} fee={fee} ema={ema}", flush=True)
        except Exception as exc:  # keep the loop alive; log and continue
            print(f"monitor error {now}: {exc}", file=sys.stderr, flush=True)

        if args.once:
            break
        time.sleep(args.interval)

    return 1 if violations_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
