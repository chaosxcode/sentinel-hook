# Sentinel — adverse-selection-aware dynamic fees for Uniswap v4

> **Static fees charge the same. LP risk does not.**

Sentinel tests whether Uniswap v4 can price adverse-selection risk better:
low fees in normal conditions, higher LP compensation when market conditions
become dangerous. The full research plan, pre-registered gates and budget are
in the **[grant application](https://chaosxcode.github.io/sentinel-hook/)**.

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

## Build and test

Built from [Uniswap v4-template](https://github.com/Uniswap/v4-template).

```bash
git clone --recurse-submodules https://github.com/chaosxcode/sentinel-hook
cd sentinel-hook
forge build
forge test -vv
```

Deploy scripts (`script/00_DeployHook.s.sol` onward) follow the template's
HookMiner flow; the first deployment target is **Unichain Sepolia**.

## Project links

- **Grant application:** https://chaosxcode.github.io/sentinel-hook/
- **Prior work — HookGuard:** transparent risk scanner for Uniswap v4 hooks:
  https://github.com/chaosxcode/hookguard

## License

MIT
