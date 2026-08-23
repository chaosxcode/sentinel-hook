"""Adverse-selection labeling for Gate 1 (prereg section 5 formulas).

All functions are pure and operate on decoded event records so they can be
unit-tested offline against synthetic fixtures.

Conventions:
- ``price`` is token1-per-token0 derived from sqrtPriceX96 and token decimals.
- A study-pool swap has direction d = +1 when the trader receives token0
  (amount0 > 0) and d = -1 when the trader pays token0.
- ``as_cost(i, h) = notional_i * d_i * (P_ref(t_i + h) - P_ref(t_i)) / P_ref(t_i)``;
  positive values mean the trade was adversely selected against LPs.
- The reference quote at time ``tau`` uses each venue's most recent print at or
  before ``tau``, weighted by that print's liquidity; the quote is stale when
  the freshest of those prints is older than the staleness bound (prereg 4).
"""

from __future__ import annotations

import bisect
import gzip
import json
from pathlib import Path
from typing import Any

STALENESS_SECONDS = 60.0


def load_events_gz(path: Path) -> list[dict[str, Any]]:
    raw = gzip.decompress(path.read_bytes())
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def attach_pool_currencies(
    events: list[dict[str, Any]], pair_by_pool: dict[str, tuple[str, str]]
) -> None:
    for row in events:
        pair = pair_by_pool.get(row["pool_id"])
        if pair is not None:
            row["_currency0"], row["_currency1"] = pair


def price_from_sqrt(sqrt_price_x96: str, decimals0: int, decimals1: int) -> float:
    sqrt_q = int(sqrt_price_x96)
    ratio = (sqrt_q / (1 << 96)) ** 2
    return ratio * (10.0 ** (decimals0 - decimals1))


class VenueSeries:
    """Per-venue sorted print lists with fast last-print-at-or-before lookup."""

    def __init__(self, decimals: dict[str, int]) -> None:
        self._decimals = decimals
        self._venues: dict[str, dict[str, list[float]]] = {}

    def add_print(self, venue_pool_id: str, timestamp: float, price: float, liquidity: int) -> None:
        venue = self._venues.setdefault(venue_pool_id, {"t": [], "p": [], "l": []})
        venue["t"].append(timestamp)
        venue["p"].append(price)
        venue["l"].append(float(max(liquidity, 1)))

    def finalize(self) -> "VenueSeries":
        for venue in self._venues.values():
            order = sorted(range(len(venue["t"])), key=lambda i: venue["t"][i])
            venue["t"] = [venue["t"][i] for i in order]
            venue["p"] = [venue["p"][i] for i in order]
            venue["l"] = [venue["l"][i] for i in order]
        return self

    def quote(self, moment: float, *, staleness: float = STALENESS_SECONDS) -> tuple[float | None, bool]:
        best: list[tuple[float, float]] = []
        freshest_age = float("inf")
        for venue in self._venues.values():
            times = venue["t"]
            if not times:
                continue
            idx = bisect.bisect_right(times, moment) - 1
            if idx < 0:
                continue
            age = moment - times[idx]
            freshest_age = min(freshest_age, age)
            best.append((venue["l"][idx], venue["p"][idx]))
        if not best or freshest_age == float("inf"):
            return None, True
        total_weight = sum(weight for weight, _ in best)
        price = sum(weight * price for weight, price in best) / total_weight
        return price, freshest_age > staleness


def build_series_from_events(
    events: list[dict[str, Any]],
    decimals: dict[str, int],
    *,
    exclude_pools: set[str] = frozenset(),
) -> VenueSeries:
    series = VenueSeries(decimals)
    for row in events:
        if row["event"] != "Swap" or row["pool_id"] in exclude_pools:
            continue
        c0 = row.get("_currency0")
        c1 = row.get("_currency1")
        d0 = decimals.get(c0)
        d1 = decimals.get(c1)
        if d0 is None or d1 is None:
            continue
        series.add_print(
            row["pool_id"],
            float(row["block_timestamp"]),
            price_from_sqrt(row["sqrt_price_x96"], d0, d1),
            int(row["liquidity"]),
        )
    return series.finalize()


def label_day(
    study_swaps: list[dict[str, Any]],
    series: VenueSeries,
    decimals0: int,
    *,
    horizon_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Label every study-pool swap with its adverse-selection cost at h."""

    labeled: list[dict[str, Any]] = []
    for swap in study_swaps:
        amount0 = int(swap["amount0"])
        direction = 1.0 if amount0 > 0 else -1.0
        moment = float(swap["block_timestamp"])
        p_before, stale_before = series.quote(moment)

        row: dict[str, Any] = {
            "pool_id": swap["pool_id"],
            "block_number": swap["block_number"],
            "transaction_hash": swap["transaction_hash"],
            "log_index": swap["log_index"],
            "timestamp": moment,
            "direction": direction,
            "abs_amount0_normalized": abs(amount0) / (10.0**decimals0),
            "status": "ok",
            "as_cost_h60": None,
            "notional_token1": None,
            "ref_move": None,
        }
        if p_before is None:
            row["status"] = "stale_reference"
            labeled.append(row)
            continue
        notional = row["abs_amount0_normalized"] * p_before
        row["notional_token1"] = notional
        if stale_before:
            # frozen rule: stale quotes are excluded from label statistics
            row["status"] = "stale_reference"
            labeled.append(row)
            continue
        p_after, stale_after = series.quote(moment + horizon_seconds)
        if p_after is None or stale_after:
            row["status"] = "missing_horizon_print"
            labeled.append(row)
            continue
        move = (p_after - p_before) / p_before
        row["ref_move"] = move
        row["as_cost_h60"] = notional * direction * move
        labeled.append(row)
    return labeled


def summarize_active_days(
    labeled_by_day: dict[str, list[dict[str, Any]]],
    *,
    min_labeled_swaps: int = 100,
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for day, rows in sorted(labeled_by_day.items()):
        usable = [r for r in rows if r["status"] == "ok"]
        loss_sum = sum(r["as_cost_h60"] for r in usable)
        summary[day] = {
            "swaps": len(rows),
            "labeled_ok": len(usable),
            "active": len(usable) >= min_labeled_swaps,
            "as_cost_sum": loss_sum,
            "positive_as_cost": bool(loss_sum > 0),
            "total_notional": sum(r["notional_token1"] for r in usable),
        }
    return summary
