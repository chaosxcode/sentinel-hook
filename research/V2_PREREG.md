# Sentinel v2 preregistration — holdout evaluation

**Frozen:** 2026-08-23 (before any holdout data was ingested)  
**Scope:** one evaluation of the calibrated SentinelHookV1 fee policy on the
locked holdout period, **Jan – Jul 2026**, evaluated exactly once.  
**Relation to Gate 1:** the Gate 1 verdict (trade-level predictability failed)
stands and is not revisited. This preregistration tests a *different*,
window-level claim that survived the post-mortem.

---

## 1. Hypothesis (single, falsifiable)

On the locked holdout period, the Sentinel v2 fee policy —
`fee(t) = clamp(4 × EMA₃₀₀ₛ[realized 60s pool-price move], 5 bps, 100 bps)` —
delivers higher net LP economics (fees collected minus adverse-selection
losses) than a static 5 bps fee on the same trades.

No trade-level predictability is claimed. The mechanism is loss clustering:
toxic windows persist, and a fee that rises with realized volatility collects
compensation while they last.

## 2. Frozen policy parameters

Chosen on Feb–Sep 2025 calibration data, confirmed on untouched Oct–Dec 2025
(`evidence/gate1/calibration-vol-fee.json`), identical to the deployed
[`SentinelHookV1`](../src/SentinelHookV1.sol):

| Parameter | Value |
|---|---|
| Signal | EMA of \|sqrtP(t) − sqrtP(t − 60s)\| / sqrtP(t − 60s) |
| EMA half-life | 300 s |
| Multiplier k | 4 |
| Base fee | 5 bps |
| Fee cap | 100 bps |
| Rate limit | 500 (0.05%) per update |

These values may not be adjusted after holdout results are seen. No parameter
search is performed on holdout data.

## 3. Data

- **Period:** all UTC days from 2026-01-01 through 2026-07-31 (the locked
  holdout; never ingested, never queried before this evaluation).
- **Pools:** the measurable cohort from the Gate 1 preregistration as amended:
  M2 native-ETH/USDC (`0x3258…d9`) and M1 USDC/HYPE (`0xc4f3…79c`), plus their
  frozen reference venues. M1's venue was active from Nov 2025, so both pools
  are expected to be measurable; any pool whose reference venues prove
  unavailable in the holdout is reported as unmeasurable, not substituted.
- **Ingestion:** identical pipeline (event extraction with anchor-verified
  timestamps, sampled day-windows: 6 per pool-month, seed `20260823`, plan
  committed before ingestion runs).
- **Labels:** identical 60-second reference-priced adverse-selection cost.

## 4. Evaluation metrics and pass bars

Primary metric: **ΔNet = LP net under v2 policy − LP net under static 5 bps**,
computed by replaying every labeled holdout trade chronologically through the
frozen policy (identical to the calibration replay; no re-fitting).

| # | Bar | Threshold |
|---|---|---|
| P1 | Pooled ΔNet > 0 | clustered bootstrap (pool-day clusters, B = 10,000, seed 20260823) 95% CI excludes 0 |
| P2 | Robustness | ΔNet > 0 in ≥ 60% of active pool-months, and positive for at least one of M2/M1 individually |
| P3 | Burden bound | average trader burden ≤ 12 bps (justification: calibration measured 8.1 bps at these parameters; above 12 bps the policy over-taxes benign flow even if profitable) |

**Pass = P1 ∧ P2 ∧ P3.** Failure of any bar = v2 rejected.

## 5. Commitment

- The holdout is ingested once and evaluated once. Re-running with different
  parameters, windows, or labels — for any reason — voids the result.
- The result is published in `FINDINGS.md` either way, with the full
  methodology and all exclusion counts.
- **On failure:** the dynamic-fee approach is abandoned. No third attempt will
  be made without a fundamentally new mechanism supported by evidence that
  predates its evaluation data.
- On success: the result supports a production-pilot proposal (Gate 3 safety
  work, audit path) — it does not by itself justify mainnet capital.

## 6. What this preregistration does not claim

- No claim that trade-level predictability exists (Gate 1 found it does not).
- No claim about trader volume response: the replay holds trade sizes fixed;
  real elasticity would change realized economics in both directions. This is
  a known limitation shared with the calibration evidence.
- No claim about pools outside the frozen cohort.

## 7. Reproduction

The holdout measurement plan, ingestion, labeling, and replay use the same
committed tooling as Gate 1 (`research/sentinel_data/`), invoked with
`--year 2026 --months 1..7`. The plan file is committed to
`evidence/gate2/` before ingestion begins; this document is committed before
that.
