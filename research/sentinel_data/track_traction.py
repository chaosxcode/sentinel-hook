#!/usr/bin/env python3
"""Daily traction snapshot for sentinel-hook — stars, forks, traffic.

Appends one JSONL line per run to evidence/traction/traction.jsonl so the
month builds a time series. Uses the authenticated gh CLI (no keys in repo).

Cron example (daily 09:00):
  0 9 * * * cd /home/x/sentinel-hook && python3 research/sentinel_data/track_traction.py
"""

import json
import subprocess
import sys
from datetime import datetime, UTC
from pathlib import Path

REPO = "chaosxcode/sentinel-hook"
OUT = Path(__file__).resolve().parents[2] / "evidence" / "traction" / "traction.jsonl"


def gh(endpoint: str):
    path = f"repos/{REPO}" + (f"/{endpoint}" if endpoint else "")
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:200])
    return json.loads(result.stdout)


def main() -> int:
    repo = gh("")
    views = gh("traffic/views")
    clones = gh("traffic/clones")
    record = {
        "t": datetime.now(UTC).isoformat(),
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "watchers": repo["subscribers_count"],
        "open_issues": repo["open_issues_count"],
        "views_14d": views["count"],
        "unique_visitors_14d": views["uniques"],
        "views_yesterday": views["views"][-1]["count"] if views["views"] else 0,
        "clones_14d": clones["count"],
        "unique_cloners_14d": clones["uniques"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
