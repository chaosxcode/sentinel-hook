<p align="center"><img src="docs/sentinel-banner.jpeg" alt="Sentinel Hook" width="100%"></p>

# Sentinel — adverse-selection-aware dynamic fees for Uniswap v4

[![Test Suite](https://github.com/chaosxcode/sentinel-hook/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/chaosxcode/sentinel-hook/actions/workflows/test.yml)

> **Static fees charge the same. LP risk does not.**

Sentinel tests whether Uniswap v4 can price adverse-selection risk better:
low fees in normal conditions, higher LP compensation when market conditions
become dangerous. The full research plan, pre-registered gates and budget are
in the **[grant application](https://chaosxcode.github.io/sentinel-hook/)**.

> **Latest research update — 2026-08-23:** the Gate 1 preregistration is now
> **frozen**: core cohort (USDC/SOL, USDC/HYPE, native ETH/USDC + three
> hash-receipted alternates), token metadata with block-pinned provenance,
> reference-price rules, adverse-selection label, leakage controls, splits and
> seeds — all fixed before any measurement. Read the
> **[Gate 1 preregistration](research/GATE1_PREREG.md)** and the
> **[dated findings log](research/FINDINGS.md)**.

## What this repo is (and deliberately is not)

This is the **V0 skeleton** referenced in the grant application. It
intentionally contains **no trained risk model and no performance claims** —
the grant funds producing that evidence, whatever the answer turns out to be.

What V0 *does* prove is the safety scaffolding the five-signal model will run
inside, working end to end on the real v4 stack:

| Safety property | Where | Verified by |
|---|---|---|
| Hard fee bounds (floor 0.01%, ceiling 1.00%) | `SentinelHookV0.sol` | fuzz: fee never leaves bounds across 6,400 randomized swaps |
| Rate-limited fee changes (≤ 0.05% per update) | `_stepToward` | fuzz + unit tests |
| Hysteresis + cooldown (no threshold bouncing) | `_beforeSwap` | `test_CooldownAndHysteresisEaseBack` |
| Pre-swap observations only — a trade cannot set its own fee | `_beforeSwap` | `test_BigMoveElevatesFee_RateLimited` |
| Safe base-fee fallback on unknown state | `_beforeSwap` | `test_UnknownPoolFallsBackToBaseFee` |
| Stale-signal recalibration | `_beforeSwap` | `test_StaleStateRecalibratesTowardBase` |
| Dynamic-fee pools only | `_beforeInitialize` | `test_RevertsOnStaticFeePool` |
| Fixed-size O(1) state, no external calls in swap path | whole contract | code review |

**Measured V0 gas overhead: ~13.9k per swap** vs an identical no-hook pool
(`test_GasOverheadVsNoHookPool`) — well inside the grant's Gate 3 target of
≤ 40k.

The V0 placeholder rule is a single tick-movement signal with two fee tiers.
It is deliberately dumb. The funded work replaces it with the five-signal
model (volatility, size pressure, deviation, imbalance, acceleration),
benchmarked in HookLab against static and volatility-based baselines on
historical data, with pre-registered pass/fail gates.

## Known V0 limitations (by design)

These are exactly the gaps the funded research closes:

- The movement signal compares consecutive swaps only — slow sustained moves
  and quiet-period gaps are not modeled (an attacker can wait out
  `STALE_AFTER`). The full model uses notional-weighted EWMA windows.
- No size-pressure, deviation, imbalance or acceleration signals yet.
- Parameters (`ENTER_TICKS`, tier fees, cooldown) are unvalidated placeholders,
  not trained values.
- No manipulation-suite testing beyond the invariant fuzz — that is Gate 3.

## Live deployment

**Unichain Sepolia:** [`0xcbd5bac7b96770d7f18b97d05d6518a4d0913080`](https://sepolia.uniscan.xyz/address/0xcbd5bac7b96770d7f18b97d05d6518a4d0913080)
(deploy tx [`0xc3e8…97f4`](https://sepolia.uniscan.xyz/tx/0xc3e802403376d05c648a28e97ded81a706696045c025a7b2232a5542a80797f4)) —
CREATE2-mined so the address encodes the hook's permission bits
(`beforeInitialize | afterInitialize | beforeSwap` = `0x3080`).

## Live demo — watch the fee logic fire on-chain

A demo dynamic-fee pool runs on Unichain Sepolia against the deployed hook
(test tokens [STA](https://sepolia.uniscan.xyz/address/0x345A187ace5808B0F7030d82cB2b444AcDa8Af1C) /
[STB](https://sepolia.uniscan.xyz/address/0x52611F5C1e35E3213E5155483311A2C9Ab310138),
pool created in [`0x2ec0db…`](https://sepolia.uniscan.xyz/tx/0x2ec0db2ba103573f8bea036706e3436020165abdaa2cdd61667684dd8e6fab78)).
The choreography in [`script/04_DemoSwaps.s.sol`](script/04_DemoSwaps.s.sol):

1. A calm swap pays the 0.05% base fee.
2. A [large swap](https://sepolia.uniscan.xyz/tx/0x50e87fd492f70b5ca0f0f4e6a31b5d7ff6d24b1b074b26f99f46c8be735f383a)
   moves the pool **−355 ticks** — and still pays the base fee, because the
   fee is decided from pre-swap state: **a trade cannot price itself**.
3. The next five swaps see that movement and ramp the fee — one rate-limited
   step per update, every step a public `FeeUpdated` event:

| Event | Fee change | Tick move seen | Transaction |
|---|---|---|---|
| 1 | 500 → 1000 | −355 | [`0x6e8f4b…`](https://sepolia.uniscan.xyz/tx/0x6e8f4bbb178d02ecf7c64a966331c6c7dea67c1f0b746a89ddef4a31b7b0b941) |
| 2 | 1000 → 1500 | +4 | [`0xb7880c…`](https://sepolia.uniscan.xyz/tx/0xb7880c35fadc957005de5a7d9be22cfe3a25d1231cb47f74a41fd269a3b1e384) |
| 3 | 1500 → 2000 | −3 | [`0xe021b8…`](https://sepolia.uniscan.xyz/tx/0xe021b81c15c729491ee70086ed849ff727a7cd2d314be5e5b158a9b8104caed7) |
| 4 | 2000 → 2500 | +3 | [`0x2ba01e…`](https://sepolia.uniscan.xyz/tx/0x2ba01e0953e5440e2d771b3acee7b27e1da9abbe7eb0b53c9588309d33ff4d38) |
| 5 | 2500 → 3000 | −3 | [`0xdc971a…`](https://sepolia.uniscan.xyz/tx/0xdc971a337241ccf2767f6dd0bf546790cec866879ed972b9fb46d78c0cc8a788) |

The pool ends at the 0.30% elevated tier (asserted on-chain by the script),
held there by hysteresis until the cooldown lets it ease back to base. Note
events 2–5: tick moves of ±3–4 keep the *target* elevated while the fee
climbs — rate limiting and hysteresis behaving exactly as the tests promise.

## Research pipeline — data ingestion and the frozen Gate 1 design

The grant-plan stages completed so far:

1. **Raw v4 event extraction** (`research/sentinel_data/`): dependency-free
   extractor that verifies chain IDs and block hashes, decodes canonical
   `Initialize`/`Swap`/`ModifyLiquidity` events, preserves large integers as
   decimal strings, and emits hash-receipted JSONL. Two fixed evidence windows
   are committed (mainnet smoke + Sepolia demo).
2. **Gate 1 preregistration freeze** (`research/GATE1_PREREG.md`): a
   deterministic selection rule run over a committed 50,000-block Unichain
   mainnet window froze the core cohort before measurement —

| Role | Pool | Swaps in window |
|---|---|---:|
| Core 1 | USDC / SOL | 8,257 |
| Core 2 | USDC / HYPE | 6,869 |
| Core 3 | ETH (native) / USDC | 5,144 |
| Alternates | ETH/USD₮0 · ETH/WBTC · WBTC/USD₮0 | 2,247 / 1,647 / 939 |

Token metadata is block-pinned; currency pairs were resolved on-chain from
ERC-20 transfer intersections of single-pool swap transactions. The prereg
also freezes reference-price alignment, the adverse-selection label, feature
windows, leakage controls, splits, seeds — and commits to publishing Gate 1
even if the thesis fails.

Run the offline receipt verifier:

```bash
python3 -m research.sentinel_data.verify \
  evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23 \
  evidence/data-pipeline/unichain-sepolia-demo
```

This is ingestion and design evidence, **not a Gate 1 result**. No reference
price has been aligned, no label computed, no signal scored — the next
checkpoint is the Gate 1 measurement itself under the frozen rules. See the
[`preregistration`](research/GATE1_PREREG.md), the
[`dated findings`](research/FINDINGS.md), and the
[`research` README](research/README.md).

## Build and test

Built from [Uniswap v4-template](https://github.com/Uniswap/v4-template).

```bash
git clone --recurse-submodules https://github.com/chaosxcode/sentinel-hook
cd sentinel-hook
forge build
forge test -vv
python3 -m unittest discover -s research/tests -v
```

Deploy scripts (`script/00_DeployHook.s.sol` onward) follow the template's
HookMiner flow; the first deployment target is **Unichain Sepolia**.

## Project links

- **Grant application:** https://chaosxcode.github.io/sentinel-hook/
- **Dated research findings:** [research/FINDINGS.md](research/FINDINGS.md)
- **Prior work — HookGuard:** transparent risk scanner for Uniswap v4 hooks:
  https://github.com/chaosxcode/hookguard

## License

MIT
