# Mainnet pilot design — Sentinel v1 on Unichain

**Frozen:** 2026-08-23 (before any mainnet deployment)  
**Precondition:** independent security review completed with no critical/high
findings unresolved. This document does NOT authorize deployment before that
precondition is met.

---

## 1. Objective

Measure the one thing the replay cannot: **real volume elasticity** — how
traders respond when the fee rises — while earning LPs the validated uplift
on a flagship pool with capped exposure.

## 2. Deployment parameters (frozen)

| Parameter | Value |
|---|---|
| Chain / pool | Unichain mainnet, native-ETH/USDC v4 pool `0x3258…d9` (the Gate 2 M2 pool) |
| Hook | `SentinelHookV1`, freshly deployed CREATE2, source-verified |
| Policy | identical to holdout: k=4, base 5bps, cap 100bps, half-life 300s, lookback 60s |
| Pilot LP capital | ≤ $250k equivalent, full-range position (measured, withdrawable) |
| Duration | 90 days from activation |
| Monitoring | offchain reader polling `getCurrentFee`/`getEmaRateWad` + event archive; public dashboard (the Lab extends to mainnet) |

## 3. Pre-registered pilot bars (evaluated at day 90)

| # | Bar |
|---|---|
| B1 | Realized net LP economics (fees − adverse selection, measured with the same 60s reference-priced label) ≥ 50% of the holdout replay's rate prediction |
| B2 | Volume retention: pilot-pool swap count ≥ 70% of the counterfactual baseline (same-pair venues' volume trend, pre-registered method) |
| B3 | Zero security incidents: no invariant violation observed live, no unexplained fee state |
| B4 | Burden: volume-weighted fee uplift ≤ 15 bps averaged over the pilot |

**Fail any bar → withdraw pilot liquidity, publish the delta between replay
and reality, revise the model.** The pilot is a measurement instrument, not
a liquidity-grab.

## 4. Kill-switch criteria (any time, no permission needed)

- Security review finding reclassified to critical post-deployment
- Invariant violation observed live (monitored continuously)
- Pool's reference venues reorganize such that the 60s label degrades
  (> 30% stale-reference for 7 consecutive days)

## 5. What success unlocks

- Gate 3 fully closed (economics on mainnet + security review)
- Scale proposal: additional pools from the frozen cohort, LP-fund
  partnerships, and a v2 signal (loss-EMA hybrid) requiring the oracle
  pathway the pilot's data will justify (or not)

## 6. Costs

- Deployment + pool + liquidity tx: < $10 (L2)
- LP capital: ≤ $250k (pilot-bounded, withdrawable)
- Security review: quoted separately (see
  [`SECURITY_REVIEW_REQUEST.md`](SECURITY_REVIEW_REQUEST.md))
- Monitoring infra: the existing public lab page extended to mainnet RPCs

---

*This design is committed before deployment. Changes after activation are
limited to the kill-switch.*
