"""Resumable Gate 1 window-ingestion orchestrator.

Wraps ``ingest_windows.ingest_day`` with scan-endpoint rotation, adaptive
cooldowns on 403/429, per-day checkpoints, and a JSONL progress log so long
runs survive rate limits without supervision. Already-completed days are
skipped, so the process can be re-run safely at any time.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .rpc import JsonRpcClient, RpcError
from .ingest_windows import ingest_day


SCAN_ENDPOINTS = [
    "https://unichain.drpc.org",
    "https://mainnet.unichain.org",
]


class EndpointPool:
    def __init__(self, urls: list[str], start_index: int = 0) -> None:
        self._clients = {url: JsonRpcClient(url, retries=4, timeout=45.0, pace=0.05) for url in urls}
        self._urls = urls
        self._index = start_index % len(urls)
        self._cooldown_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self) -> tuple[str, JsonRpcClient]:
        now = time.time()
        with self._lock:
            for _ in range(len(self._urls)):
                url = self._urls[self._index]
                self._index = (self._index + 1) % len(self._urls)
                until = self._cooldown_until.get(url, 0)
                if now >= until:
                    return url, self._clients[url]
            wait = min(self._cooldown_until.values()) - now
        if wait > 0:
            time.sleep(min(wait, 120))
        return self.acquire()

    def penalize(self, url: str, seconds: float) -> None:
        with self._lock:
            self._cooldown_until[url] = max(
                self._cooldown_until.get(url, 0), time.time() + seconds
            )


def day_is_complete(day_dir: Path) -> bool:
    manifest_path = day_dir / "manifest.json"
    events_path = day_dir / "events.jsonl.gz"
    if not (manifest_path.exists() and events_path.exists()):
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = gzip.decompress(events_path.read_bytes())
        return manifest.get("events_sha256") == hashlib.sha256(raw).hexdigest()
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--days", nargs="*", help="subset of days; default = every selected day")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))

    env_name = config.get("header_rpc_url_env")
    header_url = (
        os.environ[str(env_name)]
        if env_name and os.environ.get(str(env_name))
        else config.get("header_rpc_url")
    )
    if not header_url:
        print("no header RPC configured; refusing to run slow sequential mode", file=sys.stderr)
        return 1

    venue_map = plan.get("venue_pools", {})
    pool_ids = sorted(
        set(plan["measurable_pools"].values())
        | {pid for pids in venue_map.values() for pid in pids}
    )

    selected: set[str] = set()
    for role_days in plan["selected_days"].values():
        selected.update(role_days)
    days = sorted(selected) if not args.days else sorted(set(args.days))

    progress_lock = threading.Lock()
    stats = {"completed": 0, "skipped": 0}
    failed: dict[str, str] = {}
    args.output_root.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_root / "progress.jsonl"

    def process_day(day: str, worker_index: int) -> None:
        day_dir = args.output_root / day
        if day_is_complete(day_dir):
            with progress_lock:
                stats["skipped"] += 1
                with progress_path.open("a", encoding="utf-8") as progress:
                    progress.write(json.dumps({"day": day, "status": "skipped"}) + "\n")
                    progress.flush()
            return
        header_client = JsonRpcClient(
            str(header_url), retries=8, timeout=120.0, retry_on_rate_limit=True
        )
        pool = EndpointPool(SCAN_ENDPOINTS, start_index=worker_index)
        attempt = 0
        while attempt < 8:
            attempt += 1
            url, client = pool.acquire()
            try:
                manifest = ingest_day(
                    client,
                    header_client,
                    config,
                    plan,
                    day,
                    day_dir,
                    pool_ids=pool_ids,
                )
                with progress_lock:
                    stats["completed"] += 1
                    with progress_path.open("a", encoding="utf-8") as progress:
                        progress.write(
                            json.dumps(
                                {
                                    "day": day,
                                    "status": "ok",
                                    "endpoint": url,
                                    "records": manifest["record_count"],
                                    "blocks": manifest["distinct_event_blocks"],
                                }
                            )
                            + "\n"
                        )
                        progress.flush()
                return
            except Exception as exc:
                message = str(exc)
                penalty = (
                    600
                    if isinstance(exc, RpcError) and "429" in message or "403" in message
                    else 20
                )
                pool.penalize(url, penalty)
                with progress_lock:
                    failed[day] = message[:200]
                time.sleep(min(45, 3 * attempt))
        with progress_lock:
            with progress_path.open("a", encoding="utf-8") as progress:
                progress.write(
                    json.dumps({"day": day, "status": "failed", "error": failed[day][:160]})
                    + "\n"
                )
                progress.flush()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(process_day, day, index % len(SCAN_ENDPOINTS))
            for index, day in enumerate(days)
        ]
        for future in as_completed(futures):
            future.result()

    print(json.dumps({**stats, "failed_days": sorted(failed)}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
