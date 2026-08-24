# Sentinel — mainnet pilot proposal

**For:** Uniswap v4 DAOs / chains seeking differentiated LP infrastructure / market-neutral LP funds
**From:** Sentinel (chaosxcode) · github.com/chaosxcode/sentinel-hook
**Date:** 2026-08-23 · Status: holdout-validated, testnet-live, pre-audit

---

## The product in one sentence

A Uniswap v4 hook that **prices adverse-selection risk in real time** — fees
stay at 0.05% in calm markets and rise continuously (to 1%) exactly when
informed flow is bleeding liquidity providers — validated on data the
contract's designers never saw.

## Why anyone should care (the measured problem)

On Unichain's deepest native-ETH/USDC v4 pool, liquidity providers paid
adverse-selection costs on **126 of 126 sampled active days**. The worst 10%
of five-minute windows carried **~80% of all losses**. Static fees pay LPs
the same in calm and carnage — LPs on volatile pairs are selling insurance
at a flat price against a spiky risk.

## The evidence chain (all public, all reproducible)

| Step | Result | Artifact |
|---|---|---|
| Pre-registered Gate 1 study (2.54M labeled trades) | Problem confirmed; **v1 signal failed its bar — published as committed** | `research/FINDINGS.md` |
| Post-mortem | Toxic flow clusters in time: trailing losses → next-window losses, ρ = 0.61 | `evidence/gate1/` |
| v2 rebuild | Deployable self-contained signal (ρ = 0.36); policy beat static in **18/18** out-of-sample configurations | `evidence/gate1/calibration-vol-fee.json` |
| **Locked holdout (Jan–Jul 2026), evaluated once** | **+$366k net LP vs static; 8/8 pool-months positive; burden 9.5 bps ≤ 12** | `evidence/gate2/gate2-evaluation-results.json` |
| Contract | `SentinelHookV1` live on Unichain Sepolia, **14k gas overhead**, 257-run fuzz + 100k-step stateful campaign | `src/SentinelHookV1.sol` |

The failed Gate 1 is retained deliberately: it is proof the evaluation
machinery publishes negative results, which is exactly why the positive
holdout result is credible.

## How the hook works (no oracle, no keeper)

The hook samples its own pool price into packed 10-second buckets. Every
swap, it compares the current price to the price ~60 seconds ago and blends
that realized move into an exponentially-weighted estimate of short-horizon
volatility. The fee is a calibrated linear function of that estimate:

    fee = clamp(4 × EMA, 0.05%, 1.00%), stepped ≤ 0.05% per swap

A trade never pays a fee set by its own price impact. Hard bounds, rate
limits, and safe fallbacks are inherited from the audited-pattern V0
skeleton and covered by Foundry tests including a 100,000-step stateful
invariant campaign.

## What we are asking for

**Option A — chain / foundation grant (primary):** $60–80k to fund the
Gate 3 completion package: independent security review (Uniswap Foundation
Security Fund pathway), mainnet pilot deployment on one flagship pool with
capped liquidity, 90-day public pilot report, and HookGuard integration so
third parties can verify the hook's live behavior.

**Option B — LP fund / market-maker partnership:** deploy the hook on a
pool your LPs already run. We take no fee cut in the pilot; you keep the
measured uplift (holdout rate: ~+$50k/month net LP per $1B of routed
volume on a flagship pair, market-conditions dependent).

**Option C — acquisition / team:** the research pipeline (extraction →
labeling → calibration → holdout protocol) generalizes to any v4 deployment
and any dynamic-fee thesis. The stack is stdlib-Python + Foundry, one
developer, fully documented.

## Known limits (stated up front)

- The replay holds trade sizes fixed; real volume elasticity to higher
  fees is unmodeled. The pilot exists to measure exactly this on mainnet
  with capped exposure.
- The signal is volatility-based: it reacts to toxic flow as it happens
  (persistence is the validated edge) but does not predict individual
  toxic trades. The first trade after a quiet period always pays base.
- Pre-audit: no mainnet capital until the independent review lands.

## Contact

chaosxcode · mario18g@gmail.com ·
github.com/chaosxcode/sentinel-hook ·
live demo: chaosxcode.github.io/sentinel-hook/lab.html
