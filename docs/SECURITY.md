# Sentinel v1 — security documentation

For security reviewers, integrators, and DAOs evaluating the hook.
Contract: [`src/SentinelHookV1.sol`](../src/SentinelHookV1.sol) ·
deployment: `0x290d2d0af6dd11b6e235eac6d7528f5474753080` (Unichain Sepolia)

---

## What the hook can and cannot do

**Can:** change the pool's LP fee (within hard bounds, rate-limited) before
each swap, based on an internally-computed volatility estimate. Emit events.

**Cannot:** move user funds, touch liquidity, revert swaps by design (no
revert path in `_beforeSwap` other than storage/arith panics), make external
calls, read oracles, or act after a swap. It holds no token approvals and no
owner key. There is no admin function, no upgradeability, no pause.

## State

| Field | Location | Grows? |
|---|---|---|
| `currentFee` (uint24) | `poolState[id]` | bounded [500, 10000] |
| `lastUpdate` (uint64) | `poolState[id]` | timestamp |
| `emaRateWad` (uint128) | `poolState[id]` | bounded [0, 1e18] by clamp |
| sample ring (32 × packed ts|sqrtP) | `samples[id]` | fixed size, overwritten |

Fixed-size O(1) state per pool. Unbounded pools cannot bloat storage.

## Invariants

| ID | Invariant | Enforced by | Tested by |
|---|---|---|---|
| I1 | `BASE_FEE ≤ fee ≤ MAX_FEE` | clamp after every code path | fuzz (257 runs) + 100k-step campaign, every step |
| I2 | `|fee − prevFee| ≤ MAX_FEE_STEP` | `_stepToward` before clamps | same, every step |
| I3 | `0 ≤ emaRateWad ≤ WAD` | obs clamp + symmetric blend | same, every step |
| I4 | a trade never pays a fee set by its own impact | observation taken in `_beforeSwap` (pre-execution price) | `test_SwapCannotSetItsOwnFee` |
| I5 | unknown pool ⇒ base fee, no state write | early return | V0 suite `test_UnknownPoolFallsBackToBaseFee` |
| I6 | dynamic-fee pools only | `_beforeInitialize` revert | V0 suite `test_RevertsOnStaticFeePool` |
| I7 | stale state decays to base | time-decay EMA (linear approx, clamped) | `test_FeeDecaysBackTowardBaseAfterQuiet` |

## Verification performed (2026-08-23)

| Campaign | Scale | Result |
|---|---|---|
| Stateful fuzz (`test_StatefulCampaign100k`) | 100,000 transitions, I1–I4 asserted every step | **0 violations** |
| Unit + scenario tests | 7 (V1) + 9 (V0) + attack suite | all pass |
| Manipulation suite | 4 measured attacks | see below |
| Gas overhead vs no-hook pool | calm-swap path | 13.9–14.7k (budget 40k) |

## Manipulation findings (measured on-chain semantics)

1. **Wait-out dodge** — first trade after a quiet period pays base.
   Structural to any pre-swap signal. Sustained toxic flow re-elevates the
   fee within ≤ 80 swaps (measured). Residual risk accepted and disclosed.
2. **Volatility poisoning** — pinning the fee high costs the attacker their
   own fees every swap (~3.29 token-units per 180s window at 0.5% notional);
   fees accrue to LPs, never the attacker. Pure attacker cost.
3. **Split-trade dodge** — dusting a large trade across updates is not
   cheaper: rate limiting held ≥ 90% of the single-trade fee (measured).
4. **Mega-swap spike** — one huge trade pays base on its own notional, then
   elevates the fee for followers; elevation is bounded (decays within the
   EMA window, measured ≤ 1h under probe flow) and the attacker's own cost
   scales with their trade size.

## Known limitations

- The signal is volatility-based: it does not identify *who* is toxic, only
  *when* flow is dangerous. Benign traders in elevated windows pay elevated
  fees (measured 9.5 bps average burden in the holdout evaluation).
- Linear decay approximation of exponential decay diverges for gaps
  > ~7 minutes; clamped to full replacement (documented in code).
- No volume-elasticity modeling: the replay holds trade sizes fixed.

## Reviewer notes

- The full research failure record is public by design
  (`research/FINDINGS.md`) — Gate 1's negative result is part of the file.
- Attack-suite tests assert measured bounds; if a refactor changes any
  number, the tests fail loudly.
- Contact for review coordination: mario18g@gmail.com
