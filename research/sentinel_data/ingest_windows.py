"""Gate 1 sampled-window ingestion with hybrid RPC routing.

Log scanning runs on the configured scan endpoint (public Unichain RPC,
~2k-block chunks); block headers for every distinct event block are fetched in
batches from the header endpoint (e.g. an Alchemy key), which makes full
per-record block-hash verification and exact timestamps affordable. Each
sampled day produces a gzipped ``events.jsonl.gz`` plus a manifest pinning the
plan receipt, the uncompressed SHA-256, boundary hashes, and event counts.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .events import EVENT_TOPICS
from .rpc import JsonRpcClient


MANIFEST_SCHEMA = "sentinel.gate1-window-manifest.v1"
SCHEMA = "sentinel.v4-events.v1"
SCAN_CHUNK = 2000
HEADER_BATCH = 60
HEADER_WORKERS = 2
ANCHOR_STRIDE = 900
PROBE_EVERY_NTH_GAP = 7
MAX_PROBE_RESIDUAL_SECONDS = 3.0


def _rpc_url(config: dict[str, Any], key_env: str, url_key: str) -> tuple[str, str]:
    env_name = config.get(key_env)
    if env_name and os.environ.get(str(env_name)):
        return os.environ[str(env_name)], f"env:{env_name}"
    value = config.get(url_key)
    if value:
        return str(value), f"config:{url_key}"
    raise ValueError(f"config needs {url_key} or a populated {key_env}")


def _canonical_line(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def ingest_day(
    scan_client: JsonRpcClient,
    header_client: JsonRpcClient | None,
    config: dict[str, Any],
    plan: dict[str, Any],
    day: str,
    output_dir: Path,
    *,
    pool_ids: list[str],
) -> dict[str, Any]:
    boundary = plan["day_boundaries"].get(day)
    if boundary is None:
        raise ValueError(f"day {day} is not in the measurement plan")
    start_block = int(boundary["block"])
    next_epoch = int(boundary["epoch"]) + 86400
    end_boundary = plan["day_boundaries"].get(datetime.fromtimestamp(next_epoch, UTC).strftime("%Y-%m-%d"))
    if end_boundary is not None:
        end_block = max(start_block + 1, int(end_boundary["block"]) - 1)
    else:
        # last sampled day of the run: bound the window by one day of blocks
        end_block = start_block + 86_400

    expected_chain_id = int(config["chain_id"])
    observed_chain_id = scan_client.chain_id()
    if observed_chain_id != expected_chain_id:
        raise RuntimeError(f"scan chain id mismatch: {observed_chain_id} != {expected_chain_id}")

    topic_filter = [EVENT_TOPICS["Swap"], EVENT_TOPICS["Initialize"]]
    logs = scan_client.get_logs(
        address=str(config["pool_manager"]),
        topics=[topic_filter, [pid.lower() for pid in pool_ids]],
        from_block=start_block,
        to_block=end_block,
        chunk_size=SCAN_CHUNK,
    )
    records = [json.loads(json.dumps(log)) for log in logs if not log.get("removed", False)]
    from .events import decode_log

    decoded = []
    for log in records:
        try:
            decoded.append(decode_log(log))
        except ValueError:
            continue  # non-canonical PoolManager log inside the filter; skip defensively
    decoded.sort(key=lambda row: (row["block_number"], row["transaction_index"], row["log_index"]))

    identities = [(row["block_hash"], row["transaction_hash"], row["log_index"]) for row in decoded]
    if len(identities) != len(set(identities)):
        raise RuntimeError(f"{day}: duplicate logs returned")

    distinct_blocks = sorted({row["block_number"] for row in decoded})
    header_source = "none"
    timestamps: dict[int, int] = {}
    dense_segments: list[list[int]] = []
    probe_residuals: list[float] = []
    if decoded:
        clients: list[tuple[JsonRpcClient | None, str]] = [(header_client, "header_rpc")]
        if header_client is None:
            clients = [(scan_client, "scan_rpc")]
        used = False
        for hc, source in clients:
            if hc is None or used:
                continue
            observed = hc.chain_id()
            if observed != expected_chain_id:
                raise RuntimeError(f"header chain id mismatch: {observed} != {expected_chain_id}")

            lo, hi = distinct_blocks[0], distinct_blocks[-1]
            anchors = sorted(
                set([lo, hi]) | set(range(lo - (lo % ANCHOR_STRIDE), hi + 1, ANCHOR_STRIDE))
            )
            anchors = [a for a in anchors if a <= hi]
            anchor_headers = hc.get_blocks(anchors, batch_size=HEADER_BATCH, workers=HEADER_WORKERS)
            # piecewise-linear interpolation between verified anchor headers
            def interp(block: int) -> float:
                import bisect as _bisect

                positions = _bisect.bisect_right(anchors, block) - 1
                left_a = anchors[max(positions, 0)]
                right_a = anchors[min(positions + 1, len(anchors) - 1)]
                t_left = anchor_headers[left_a].timestamp
                t_right = anchor_headers[right_a].timestamp
                if right_a == left_a or t_right < t_left:
                    return float(t_left)
                frac = (block - left_a) / (right_a - left_a)
                return t_left + frac * (t_right - t_left)

            gaps = list(range(len(anchors) - 1))
            probe_positions = [
                anchors[i]
                for i in gaps
                if (i + 1) % PROBE_EVERY_NTH_GAP == 0 and anchors[i + 1] - anchors[i] > 2
            ]
            probes = []
            for a in probe_positions:
                midpoint = (a + anchors[anchors.index(a) + 1]) // 2
                probes.append(midpoint)
            probe_headers = (
                hc.get_blocks(probes, batch_size=HEADER_BATCH, workers=HEADER_WORKERS)
                if probes
                else {}
            )
            bad_segments: set[int] = set()
            for midpoint in probes:
                idx = max(i for i, a in enumerate(anchors) if a <= midpoint)
                residual = abs(interp(midpoint) - probe_headers[midpoint].timestamp)
                probe_residuals.append(residual)
                if residual > MAX_PROBE_RESIDUAL_SECONDS:
                    bad_segments.add(idx)
                    probe_residuals.append(-residual)  # marker: segment densified
            dense_blocks: set[int] = set()
            for seg in bad_segments:
                seg_lo, seg_hi = anchors[seg], anchors[seg + 1]
                dense_blocks.update(b for b in distinct_blocks if seg_lo <= b <= seg_hi)
                dense_segments.append([seg_lo, seg_hi])
            if dense_blocks:
                dense_headers = hc.get_blocks(sorted(dense_blocks), batch_size=HEADER_BATCH, workers=HEADER_WORKERS)
                for block in sorted(dense_blocks):
                    timestamps[block] = dense_headers[block].timestamp
            for block in distinct_blocks:
                if block not in timestamps:
                    timestamps[block] = round(interp(block))

            for row in decoded:
                row["schema"] = SCHEMA
                row["chain_id"] = observed_chain_id
                row["block_timestamp"] = timestamps[row["block_number"]]
                from datetime import datetime as _dt, timezone as _tz

                row["block_timestamp_iso"] = _dt.fromtimestamp(
                    row["block_timestamp"], _tz.utc
                ).isoformat()
            header_source = source
            used = True

    events_content = b"".join(_canonical_line(row) for row in decoded)
    events_hash = hashlib.sha256(events_content).hexdigest()
    counts: dict[str, int] = {}
    for row in decoded:
        counts[row["event"]] = counts.get(row["event"], 0) + 1

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": f"gate1-{plan['name']}-{day}",
        "chain_id": observed_chain_id,
        "pool_manager": str(config["pool_manager"]).lower(),
        "plan_sha256": plan["plan_sha256"],
        "day": day,
        "range": {
            "from_block": start_block,
            "to_block": end_block,
            "from_block_hash": boundary["block_hash"],
            "from_block_timestamp": boundary["block_timestamp"],
        },
        "scanned_pool_ids": sorted(pid.lower() for pid in pool_ids),
        "verification": "anchor-verified",
        "anchor_stride_blocks": ANCHOR_STRIDE,
        "probe_residuals_seconds": [round(r, 3) for r in probe_residuals],
        "max_probe_residual_seconds": (
            max((r for r in probe_residuals if r > 0), default=0.0)
        ),
        "dense_refetched_segments": dense_segments,
        "header_rpc_source": header_source,
        "record_count": len(decoded),
        "event_counts": dict(sorted(counts.items())),
        "distinct_event_blocks": len(distinct_blocks),
        "events_file": "events.jsonl.gz",
        "events_sha256": events_hash,
        "generated_at": datetime.now(UTC).isoformat(),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_events = output_dir / "events.jsonl.gz.tmp"
    with open(tmp_events, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as gz:
            gz.write(events_content)
    os.replace(tmp_events, output_dir / "events.jsonl.gz")
    tmp_manifest = output_dir / "manifest.json.tmp"
    tmp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, output_dir / "manifest.json")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--days", nargs="*", help="subset of YYYY-MM-DD days; default = all in plan")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        scan_url, _ = _rpc_url(config, "rpc_url_env", "rpc_url")
        header_url = None
        env_name = config.get("header_rpc_url_env")
        if env_name and os.environ.get(str(env_name)):
            header_url = os.environ[str(env_name)]
        elif config.get("header_rpc_url"):
            header_url = str(config["header_rpc_url"])
        scan_client = JsonRpcClient(scan_url)
        header_client = JsonRpcClient(header_url) if header_url else None
        venue_map = plan.get("venue_pools", {})
        venue_pids = [pid for pids in venue_map.values() for pid in pids]
        pool_ids = sorted(set(plan["measurable_pools"].values()) | set(venue_pids))
        days = args.days or sorted(plan["day_boundaries"])
        failures = []
        for day in days:
            try:
                manifest = ingest_day(
                    scan_client,
                    header_client,
                    config,
                    plan,
                    day,
                    args.output_root / day,
                    pool_ids=pool_ids,
                )
                print(
                    json.dumps(
                        {
                            "day": day,
                            "records": manifest["record_count"],
                            "blocks": manifest["distinct_event_blocks"],
                            "counts": manifest["event_counts"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:  # per-day isolation: one bad day must not stop the run.
                failures.append({"day": day, "error": str(exc)})
                print(f"FAILED {day}: {exc}", file=sys.stderr, flush=True)
        if failures:
            print(json.dumps({"failures": failures}), file=sys.stderr)
            return 1
        return 0
    except Exception as exc:  # CLI boundary
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
