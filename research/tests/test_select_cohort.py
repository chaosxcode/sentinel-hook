from __future__ import annotations

import unittest

from research.sentinel_data.select_cohort import (
    COHORT_RULE,
    _swap_keys_for_pool,
    compute_pool_stats,
    rank_pools,
    resolve_pair_via_transfers,
    select_cohort,
)


def _swap(pool_id: str, block: int, log_index: int) -> dict[str, str | int]:
    return {"event": "Swap", "pool_id": pool_id, "block_number": block, "log_index": log_index}


def _modify(pool_id: str, block: int, log_index: int) -> dict[str, str | int]:
    return {
        "event": "ModifyLiquidity",
        "pool_id": pool_id,
        "block_number": block,
        "log_index": log_index,
    }


class ComputePoolStatsTests(unittest.TestCase):
    def test_counts_swaps_modifies_and_initializes(self) -> None:
        records = [
            _swap("0x" + "aa" * 32, 1, 0),
            _swap("0x" + "aa" * 32, 2, 1),
            _modify("0x" + "aa" * 32, 3, 0),
            {"event": "Initialize", "pool_id": "0x" + "bb" * 32},
        ]
        stats = compute_pool_stats(records)
        self.assertEqual(stats["0x" + "aa" * 32]["swap_count"], 2)
        self.assertEqual(stats["0x" + "aa" * 32]["modify_liquidity_count"], 1)
        self.assertEqual(stats["0x" + "aa" * 32]["initialize_observed"], 0)
        self.assertEqual(stats["0x" + "bb" * 32]["initialize_observed"], 1)

    def test_rank_is_swap_desc_then_pool_id_asc(self) -> None:
        low = "0x" + "01" * 32
        high = "0x" + "02" * 32
        tie_a = "0x" + "03" * 32
        tie_b = "0x" + "04" * 32
        records = (
            [_swap(low, i, 0) for i in range(10)]
            + [_swap(high, i, 0) for i in range(50)]
            + [_swap(tie_b, i, 0) for i in range(30)]
            + [_swap(tie_a, i, 0) for i in range(30)]
        )
        stats = compute_pool_stats(records)
        self.assertEqual(rank_pools(stats), [high, tie_a, tie_b, low])


class SelectCohortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pools = {
            "core1": ["0x" + "a1" * 20, "0x" + "b1" * 20],
            "core2": ["0x" + "a2" * 20, "0x" + "b2" * 20],
            "core3": ["0x" + "a3" * 20, "0x" + "b3" * 20],
            "alt1": ["0x" + "a4" * 20, "0x" + "b4" * 20],
            "alt2": ["0x" + "a5" * 20, "0x" + "b5" * 20],
            "alt3": ["0x" + "a6" * 20, "0x" + "b6" * 20],
            "dup_pair": ["0x" + "a1" * 20, "0x" + "b1" * 20],
            "unresolved": [],
        }
        counts = {
            "core1": 9000,
            "core2": 8000,
            "core3": 7000,
            "dup_pair": 6000,
            "alt1": 5000,
            "alt2": 4000,
            "alt3": 3000,
            "unresolved": 2500,
            "below_cutoff": 100,
        }
        # Ranked order is swap-count desc then id asc; give ids that order.
        self.stats: dict[str, dict[str, int]] = {}
        for position, (name, count) in enumerate(counts.items()):
            pool_id = "0x" + f"{100 - position:06d}" + name.encode().hex().rjust(56, "0")
            self.stats[pool_id] = {
                "swap_count": count,
                "modify_liquidity_count": 1,
                "initialize_observed": 0,
            }
            setattr(self, f"id_{name}", pool_id)
        self.ranked = rank_pools(self.stats)
        self.expected_ranked = [
            getattr(self, f"id_{name}")
            for name in ("core1", "core2", "core3", "dup_pair", "alt1", "alt2", "alt3", "unresolved")
        ]

    def _resolver(self, pool_id: str) -> tuple[list[str], str]:
        for name, pair in self.pools.items():
            if pool_id == getattr(self, f"id_{name}", None):
                return (sorted(pair), "resolved") if pair else ([], "no_transfer_logs_found")
        raise AssertionError(f"unexpected pool {pool_id}")

    def test_selects_core_alternates_and_excludes_correctly(self) -> None:
        core, alternates, excluded = select_cohort(self.ranked, self.stats, self._resolver)
        self.assertEqual(
            [row["pool_id"] for row in core],
            self.expected_ranked[:3],
        )
        self.assertEqual(
            [row["pool_id"] for row in alternates],
            self.expected_ranked[4:7],
        )
        reasons = {row["pool_id"]: row["exclusion_reason"] for row in excluded}
        self.assertEqual(reasons[self.id_dup_pair], "duplicate_currency_pair")
        # Selection stops as soon as core + alternates are filled; the
        # unresolved pool ranks below that point and is simply never reached.
        all_selected = {row["pool_id"] for row in core + alternates}
        self.assertNotIn(self.id_unresolved, all_selected)
        self.assertNotIn(self.id_unresolved, reasons)

    def test_stops_at_min_swaps_cutoff(self) -> None:
        core, alternates, excluded = select_cohort(self.ranked, self.stats, self._resolver)
        all_selected = {row["pool_id"] for row in core + alternates}
        self.assertNotIn(self.id_below_cutoff, all_selected)

    def test_rule_is_frozen_v1(self) -> None:
        self.assertEqual(COHORT_RULE["rule_id"], "sentinel-cohort-rule-v1")
        self.assertEqual(COHORT_RULE["min_swaps_in_window"], 500)
        self.assertEqual(COHORT_RULE["core_pools"], 3)
        self.assertEqual(COHORT_RULE["alternate_pools"], 3)


class CleanSampleTests(unittest.TestCase):
    def test_swap_keys_skip_multi_pool_transactions(self) -> None:
        pool_a = "0x" + "aa" * 32
        pool_b = "0x" + "bb" * 32
        records = [
            {
                "event": "Swap",
                "pool_id": pool_a,
                "block_number": 1,
                "transaction_index": 0,
                "transaction_hash": "0xclean",
            },
            {
                "event": "Swap",
                "pool_id": pool_b,
                "block_number": 2,
                "transaction_index": 0,
                "transaction_hash": "0xdirty",
            },
            {
                "event": "Swap",
                "pool_id": pool_a,
                "block_number": 2,
                "transaction_index": 0,
                "transaction_hash": "0xdirty",
            },
            {
                "event": "Swap",
                "pool_id": pool_a,
                "block_number": 3,
                "transaction_index": 0,
                "transaction_hash": "0xalso_clean",
            },
        ]
        keys = _swap_keys_for_pool(records, pool_a)
        self.assertEqual([key[2] for key in keys], ["0xclean", "0xalso_clean"])


class TransferIntersectionTests(unittest.TestCase):
    class StubClient:
        def __init__(self, tokens_by_tx: dict[str, set[str]]) -> None:
            self.tokens_by_tx = tokens_by_tx

        def transaction_receipt(self, tx_hash: str) -> dict[str, object]:
            pm = "0x" + "f" * 40
            transfer_topic = (
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
            )
            logs = [
                {
                    "address": token,
                    "topics": [transfer_topic, "0x" + "11" * 32, "0x" + pm.rjust(64, "0")],
                }
                for token in self.tokens_by_tx[tx_hash]
            ]
            return {"logs": logs}

    def test_intersection_over_clean_samples(self) -> None:
        token_x = "0x" + "aa" * 20
        token_y = "0x" + "bb" * 20
        client = self.StubClient(
            {
                "0xtx1": {token_x, token_y},
                "0xtx2": {token_x, token_y},
            }
        )
        currencies, status = resolve_pair_via_transfers(
            client,
            "0x" + "f" * 40,
            "0xp",
            [(1, 0, "0xtx1"), (2, 0, "0xtx2")],
            max_samples=5,
            min_samples=3,
        )
        self.assertEqual(status, "insufficient_clean_samples:2")

    def test_resolves_with_enough_samples(self) -> None:
        token_x = "0x" + "aa" * 20
        token_y = "0x" + "bb" * 20
        client = self.StubClient({f"0xtx{i}": {token_x, token_y} for i in range(4)})
        currencies, status = resolve_pair_via_transfers(
            client,
            "0x" + "f" * 40,
            "0xp",
            [(i, 0, f"0xtx{i}") for i in range(4)],
            max_samples=5,
            min_samples=3,
        )
        self.assertEqual(status, "resolved")
        self.assertEqual(currencies, sorted([token_x, token_y]))

    def test_single_token_resolves_as_native_eth_pair(self) -> None:
        token_x = "0x" + "aa" * 20
        zero = "0x" + "00" * 20
        client = self.StubClient({f"0xtx{i}": {token_x} for i in range(4)})
        currencies, status = resolve_pair_via_transfers(
            client,
            "0x" + "f" * 40,
            "0xp",
            [(i, 0, f"0xtx{i}") for i in range(4)],
            max_samples=5,
            min_samples=3,
        )
        self.assertEqual(status, "resolved_with_native_currency")
        self.assertEqual(currencies, sorted([zero, token_x]))


if __name__ == "__main__":
    unittest.main()
