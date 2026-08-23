from __future__ import annotations

import unittest

from research.sentinel_data.gate1_metrics import (
    clustered_bootstrap_rho,
    ewma_volatility,
    gate1_criteria,
    rolling_flow_imbalance,
    spearman,
    zscore_within_day,
)


class EwmaTests(unittest.TestCase):
    def test_constant_prices_give_zero_vol(self) -> None:
        times, vols = ewma_volatility(
            [0.0, 5.0], [100.0, 100.0], half_life=30.0, step_seconds=1.0
        )
        self.assertEqual(len(vols), 6)
        finite = [v for v in vols if v == v]
        self.assertTrue(all(v == 0.0 for v in finite))

    def test_jump_raises_vol_then_decays(self) -> None:
        times = [float(t) for t in range(0, 120)]
        prices = [100.0] * 60 + [110.0] * 60
        _, vols = ewma_volatility(times, prices, half_life=10.0)
        after_jump = vols[61]
        later = vols[119]
        self.assertGreater(after_jump, 0)
        self.assertLess(later, after_jump)


class FlowImbalanceTests(unittest.TestCase):
    def test_all_buys_positive_all_sells_negative(self) -> None:
        rows = [
            {"timestamp": i * 10, "log_index": 0, "direction": d, "notional_token1": 100.0}
            for i, d in enumerate([1, 1, 1])
        ]
        flows = rolling_flow_imbalance(rows, window_seconds=30.0)
        self.assertEqual(flows[0], 0.0)
        self.assertAlmostEqual(flows[1], 1.0)
        rows_sell = [
            {"timestamp": i * 10, "log_index": 0, "direction": -d, "notional_token1": 100.0}
            for i, d in enumerate([1, 1, 1])
        ]
        flows_sell = rolling_flow_imbalance(rows_sell, window_seconds=30.0)
        self.assertAlmostEqual(flows_sell[2], -1.0)

    def test_never_sees_itself_or_later_trades(self) -> None:
        rows = [
            {"timestamp": 5, "log_index": 0, "direction": 1, "notional_token1": 50.0},
            {"timestamp": 5, "log_index": 1, "direction": 1, "notional_token1": 50.0},
        ]
        flows = rolling_flow_imbalance(rows, window_seconds=30.0)
        self.assertEqual(flows[0], 0.0)  # nothing strictly before
        self.assertAlmostEqual(flows[1], 1.0)  # sees only first trade


class StatsTests(unittest.TestCase):
    def test_spearman_perfect_and_inverse(self) -> None:
        x = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(spearman(x, [10.0, 20.0, 30.0, 40.0]), 1.0)
        self.assertAlmostEqual(spearman(x, [-1.0, -2.0, -3.0, -4.0]), -1.0)

    def test_zscore(self) -> None:
        zs = zscore_within_day([1.0, 2.0, 3.0])
        self.assertAlmostEqual(sum(zs), 0.0, places=9)

    def test_bootstrap_detects_strong_signal(self) -> None:
        rows = []
        for day in range(4):
            for i in range(50):
                rows.append(
                    {
                        "day_pool": f"p{day}",
                        "score": float(i) + day * 1000,
                        "as_cost_h60": float(i) + day * 100,
                    }
                )
        result = clustered_bootstrap_rho(rows, iterations=500)
        self.assertGreater(result["rho"], 0.9)
        self.assertTrue(result["excludes_zero"])


class CriteriaTests(unittest.TestCase):
    def test_all_three_bars(self) -> None:
        days = [
            {"positive_as_cost": True},
            {"positive_as_cost": True},
            {"positive_as_cost": False},
            {"positive_as_cost": True},
        ]
        windows_by_day = {"a": [5.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]}
        boot = {"rho": 0.3, "ci_low": 0.1, "ci_high": 0.5, "excludes_zero": True}
        result = gate1_criteria(days, windows_by_day, boot)
        self.assertTrue(result["c1_pass"])
        self.assertTrue(result["c2_pass"])  # top decile holds most loss
        self.assertTrue(result["c3_pass"])
        self.assertTrue(result["gate1_pass"] is False)  # never auto-passes here


if __name__ == "__main__":
    unittest.main()
