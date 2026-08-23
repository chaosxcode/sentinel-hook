from __future__ import annotations

import unittest

from research.sentinel_data.labels import (
    VenueSeries,
    label_day,
    price_from_sqrt,
    summarize_active_days,
)


def _swap(amount0: int, timestamp: float) -> dict[str, object]:
    return {
        "event": "Swap",
        "pool_id": "0x" + "aa" * 20,
        "amount0": str(amount0),
        "block_timestamp": int(timestamp),
        "sqrt_price_x96": "0" ,
        "liquidity": "0",
        "block_number": 1,
        "transaction_hash": "0xtx",
        "log_index": 0,
    }


class PriceMathTests(unittest.TestCase):
    def test_equal_decimals_price_is_ratio_squared(self) -> None:
        # sqrtPriceX96 = 2^96 -> ratio = 1 -> price = 1
        self.assertAlmostEqual(price_from_sqrt(str(1 << 96), 18, 18), 1.0)
        # sqrtPriceX96 = 2^97 -> ratio = 4 (price doubles when sqrt doubles x2)
        self.assertAlmostEqual(price_from_sqrt(str(1 << 97), 18, 18), 4.0)

    def test_decimal_adjustment(self) -> None:
        # token1-per-token0 scales by 10^(d0-d1)
        self.assertAlmostEqual(price_from_sqrt(str(1 << 96), 6, 18), 1e-12)
        self.assertAlmostEqual(price_from_sqrt(str(1 << 96), 18, 6), 1e12)


class VenueSeriesTests(unittest.TestCase):
    def test_quote_uses_latest_print_per_venue_weighted(self) -> None:
        s = VenueSeries({}).finalize()
        s.add_print("venueA", 10.0, 100.0, 3)
        s.add_print("venueA", 50.0, 200.0, 1)
        s.add_print("venueB", 40.0, 190.0, 3)
        s.finalize()
        price, stale = s.quote(55.0)
        self.assertFalse(stale)
        self.assertAlmostEqual(price, (1 * 200.0 + 3 * 190.0) / 4)
        price_b, stale_b = s.quote(45.0)
        self.assertAlmostEqual(price_b, (3 * 100.0 + 3 * 190.0) / 6)

    def test_staleness_bound(self) -> None:
        s = VenueSeries({}).finalize()
        s.add_print("venueA", 0.0, 100.0, 1)
        s.finalize()
        _, stale = s.quote(30.0)
        self.assertFalse(stale)
        _, stale_late = s.quote(61.0)
        self.assertTrue(stale_late)
        old_price, stale_old = s.quote(1000.0)
        self.assertEqual(old_price, 100.0)
        self.assertTrue(stale_old)


class LabelDayTests(unittest.TestCase):
    def _series(self) -> VenueSeries:
        s = VenueSeries({})
        # venue price steps from 100 to 110 at t=455
        s.add_print("v", 398.0, 100.0, 5)
        s.add_print("v", 455.0, 110.0, 5)
        return s.finalize()

    def test_buyer_before_upmove_has_positive_as_cost(self) -> None:
        swaps = [_swap(+5_000_000, 400)]  # trader buys token0 at t=400; ref rises by t=460
        rows = label_day(swaps, self._series(), decimals0=6, horizon_seconds=60)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertGreater(rows[0]["as_cost_h60"], 0)

    def test_seller_in_downmarket_has_negative_as_cost(self) -> None:
        swaps = [_swap(-5_000_000, 450)]
        rows = label_day(swaps, self._series(), decimals0=6, horizon_seconds=60)
        self.assertEqual(rows[0]["status"], "ok")
        # seller (d=-1) against a rising reference move -> negative cost
        self.assertLess(rows[0]["as_cost_h60"], 0)

    def test_missing_horizon_print_is_excluded(self) -> None:
        swaps = [_swap(+1_000_000, 500)]
        rows = label_day(swaps, self._series(), decimals0=6, horizon_seconds=60)
        self.assertEqual(rows[0]["status"], "missing_horizon_print")

    def test_stale_reference_excluded(self) -> None:
        s = VenueSeries({}).finalize()
        s.add_print("v", 0.0, 100.0, 5)
        s.finalize()
        rows = label_day([_swap(+1, 70)], s, decimals0=6)
        self.assertEqual(rows[0]["status"], "stale_reference")
        self.assertIsNone(rows[0]["as_cost_h60"])


class ActiveDayTests(unittest.TestCase):
    def test_threshold_and_signs(self) -> None:
        good = {"status": "ok", "as_cost_h60": 5.0, "notional_token1": 100.0}
        bad = {"status": "stale_reference", "as_cost_h60": None, "notional_token1": None}
        summary = summarize_active_days({"2025-01-01": [good] * 100 + [bad] * 7})
        row = summary["2025-01-01"]
        self.assertTrue(row["active"])
        self.assertEqual(row["labeled_ok"], 100)
        self.assertTrue(row["positive_as_cost"])
        thin = summarize_active_days({"2025-01-02": [good] * 99})
        self.assertFalse(thin["2025-01-02"]["active"])


if __name__ == "__main__":
    unittest.main()
