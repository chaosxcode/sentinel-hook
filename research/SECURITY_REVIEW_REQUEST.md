# Independent security review — engagement request (draft)

**Project:** SentinelHookV1 — adverse-selection-aware dynamic-fee hook for
Uniswap v4
**Sponsor:** chaosxcode (independent researcher) · mario18g@gmail.com
**Target venue:** Uniswap Foundation Security Fund; open to accredited
audit firms (suggested scope tier: solo senior review, 2–3 weeks)

---

## 1. Scope

- `src/SentinelHookV1.sol` (~250 lines, single contract, no inheritance
  beyond OpenZeppelin `BaseHook`, no external calls, no admin surface)
- Interaction surface: v4 `beforeInitialize` / `afterInitialize` /
  `beforeSwap` only; fee override return path
- Storage: one packed struct + 32-slot packed sample ring per pool

Out of scope (already reviewed/tested separately): v4 core, periphery
router, position manager.

## 2. What we provide

- Full Foundry suite: 16 tests including a 100,000-step stateful fuzz
  campaign with per-step invariant assertions (bounds, rate limit, EMA
  range, state consistency) and a 4-attack measured manipulation suite
- Complete research record including a published negative result (Gate 1),
  calibration artifacts, and a holdout evaluation run exactly once under a
  frozen protocol
- This document + `docs/SECURITY.md` (invariant/enforcement map)
- Deployment address with verified creation tx on Unichain Sepolia

## 3. Specific questions we want answered

1. Is the linear-decay approximation of the EMA exploitable at gap
   boundaries (decay clamp at `decayWad ≥ WAD`)?
2. Can the sample ring be poisoned across bucket boundaries to bias the
   60-second lookback reference?
3. Is the symmetric EMA blend (`obs ≥ ema` branch) reentrancy-safe in the
   context of hooks that return deltas (we return none)?
4. Any path where `getCurrentFee` diverges from the fee actually applied by
   the PoolManager?
5. Gas-griefing: worst-case cost of `_beforeSwap` under adversarial bucket
   distribution.

## 4. Compensation & terms

Standard UF Security Fund terms preferred. Report will be published
(in-full or in-redacted-summary, reviewer's choice) alongside our public
research record. No confidentiality clause that prevents publishing the
existence of the review.

---

*This request is a draft prepared for sponsor review; figures and scope can
be adjusted to the reviewer's standard template.*
