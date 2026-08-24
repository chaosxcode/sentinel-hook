# Sentinel research findings

**Last updated:** 2026-08-23  
**Current stage:** Sentinel v2 **passed its pre-registered holdout evaluation** (Gate 2, P1–P3)  
**Gate status:** Gate 1 failed (published) → v2 rebuilt → **holdout PASS** → production-pilot design next

This is a dated, evidence-linked research log for grant reviewers and other
builders following Sentinel's progress. Findings are separated from hypotheses
so implementation progress cannot be mistaken for economic validation.

## 2026-08-23 — Gate 2 holdout result: PASS (all three bars)

Sentinel v2 was evaluated exactly once on the locked holdout (Jan–Jul 2026)
under [`V2_PREREG.md`](V2_PREREG.md), frozen before ingestion. **All three
pre-registered bars passed.** Plan SHA-256 `21d2e6e1…002f5e`; 110 sampled
day-windows ingested with zero failures; 1,243,673 labeled trades across 161
pool-day clusters.

| Bar | Requirement | Measured | Verdict |
|---|---|---|---|
| P1 | pooled ΔNet > 0, clustered-bootstrap 95% CI excludes 0 | **ΔNet +$366,054**; mean uplift 10.00 bps, CI [7.74, 12.61] | **PASS** |
| P2 | ΔNet > 0 in ≥60% of active pool-months and ≥1 pool | **8/8 pool-months positive**; M2 +$365,453 and M1 +$602 both positive | **PASS** |
| P3 | trader burden ≤ 12 bps | **9.54 bps** | **PASS** |

Per-pool diagnostics: M2 (native ETH/USDC) precision 0.65, toxic-loss
coverage 2.76×; M1 (USDC/HYPE) precision 0.56, coverage 4.41×. Pooled
recapture ratio 2.77× — the policy collected 2.77× the adverse-selection
losses it measured, at 9.5 bps average trader burden.

### Honest reading (stated plainly)

- P1 is close to mechanical in a fixed-trade replay: the dynamic fee is
  never below base, so uplift cannot be negative. The informative bars are
  P3 (burden stayed inside the pre-committed bound) and the diagnostics
  (two-thirds of fee uplift landed on genuinely toxic trades; coverage
  exceeded ongoing losses during elevated windows).
- The replay holds trade sizes fixed — real volume elasticity is unmodeled
  and cuts in both directions (disclosed in V2_PREREG §6).
- Holdout-period adverse selection ($132k on sampled days) was much milder
  than the validation year ($8.1M) — the policy passed in a *calmer*
  market than the one that motivated it.

### Consequence

Per V2_PREREG §5: the result supports a production-pilot proposal — Gate 3
security work (extended fuzzing, independent review) and a mainnet-pilot
design. It does not by itself justify mainnet capital.

Full results: [`evidence/gate2/gate2-evaluation-results.json`](../evidence/gate2/gate2-evaluation-results.json).

## 2026-08-23 — Sentinel v2: deployable signal found, calibrated, and shipped

Following the Gate 1 failure, development focused on one question: **can a
fee policy that an on-chain hook can actually compute still beat static
fees?** Three results, each with a committed artifact:

### 1. The deployable signal had to be discovered, and one candidate died

External reference prices are unavailable to a hook, so the first candidate
was swap-to-swap self-price drift. **Rejected**: tick quantization makes
consecutive-swap drift nearly zero on the deep native-ETH/USDC pool, and the
signal showed no correlation with reference-priced losses
(ρ ≈ −0.07; [`self-drift-signal-validation.json`](../evidence/gate1/self-drift-signal-validation.json)).

The accepted signal is **EMA[half-life 300s] of realized 60-second pool-price
moves** (|ΔsqrtP| over a 60s lookback): ρ = **0.36** against reference-priced
losses at 60–120s lookbacks
([`self-drift-validation-lb60.json`](../evidence/gate1/self-drift-validation-lb60.json)) —
recovering the full predictive strength of the volatility component that
survived Gate 1, entirely from the hook's own state.

### 2. The deployable policy beats static fees out-of-sample

Calibrated on Feb–Sep 2025 labeled trades, evaluated on untouched Oct–Dec
([`calibration-vol-fee.json`](../evidence/gate1/calibration-vol-fee.json)):

| Config (k / cap) | Net LP (eval) | Precision | Toxic-loss coverage | Burden |
|---|---:|---:|---:|---:|
| 4 / 100bps | **+$2.98M** vs static | 0.71 | 1.98× | 8.1 bps |
| 2 / 100bps | +$0.84M vs static | 0.70 | 0.56× | 2.3 bps |
| 8 / 100bps | +$7.62M vs static | 0.71 | 5.05× | 20.7 bps |

Monotone in k, stable precision across the sweep. The reference-priced
(oracle) variant of the same policy had scored +$4.52M — the deployable
signal retains ~66% of the oracle's economics with zero external dependencies.

### 3. SentinelHookV1 is implemented and tested

[`src/SentinelHookV1.sol`](../src/SentinelHookV1.sol): continuous policy with
packed 10-second sample ring (one SSTORE per swap), time-decay EMA, and every
V0 safety rail (hard bounds, rate-limited stepping, safe fallback,
pre-swap observation). Foundry suite: 7 tests including a 257-run fuzz on
fee bounds and rate-limit invariants. **Measured swap-path overhead:
13.9–14.7k gas vs a no-hook pool** — inside the ≤40k Gate 3 budget.

### What this does not establish

All v2 evidence comes from calendar-2025 development data that informed the
design. The locked holdout (Jan–Jul 2026) has not been ingested or evaluated
and will be used exactly once, under
[`V2_PREREG.md`](V2_PREREG.md) — frozen 2026-08-23 with the policy parameters,
pass bars (P1 significance, P2 robustness, P3 burden bound), and the
one-shot commitment — before any performance claim is made.

## 2026-08-23 — Gate 1 validation result: FAIL on criterion 3

Sentinel ran its full pre-registered Gate 1 measurement over calendar-2025
validation data and publishes the result under the frozen commitment: **the
live-hook thesis is terminated.** Two of three bars passed decisively — LP
pain on core Unichain v4 pools is real, persistent, and concentrated — but the
pre-signal composite showed essentially no trade-level predictability of that
pain, far below the registered bar. Per the grant application and
[`GATE1_PREREG.md`](GATE1_PREREG.md) §5: *"Failure of any criterion = Gate 1
failed; methodology and negative results are published and the live-hook
thesis terminates."*

### What ran

| Item | Value |
|---|---|
| Frozen design | [`GATE1_PREREG.md`](GATE1_PREREG.md), plan SHA-256 `1188cac2…354aab0fa6` |
| Measurable cohort | M1 USDC/HYPE (`0xc4f3…79c`), M2 ETH(native)/USDC (`0x3258…d9`) |
| Sampling | 6 seeded day-windows per active month, 123 unique windows ingested |
| Ingested events | ~4M decoded PoolManager events across 123 day-windows, 0 failed days |
| Labeled trades (ok) | 2,536,933 (M2: 2,530,814 · M1: 6,119) |
| Label horizon | 60 s against depth-weighted same-pair venue reference prices |
| Verification | Boundary/anchor block hashes + probe residuals (max seen: 0.000 s); one trade re-derived by hand from raw events matched to 10⁻⁹ |

Cohort changes, both forced by data availability before labels were computed
and both documented in prereg amendments: USDC/SOL had no same-pair reference
venue anywhere in the ranked universe (substituted by alternate A1 =
ETH(native)/USD₮0 per fixed order); the substituted M3's own reference venues
turned out too thin during 2025 (e.g. 15,333 study swaps vs 0 venue prints on
2025-08-16), so every M3 label was stale-by-rule and M3 contributes zero
labeled trades. No post-hoc substitutions were made after any outcome was
seen.

### The three pre-registered criteria

| Criterion | Bar | Measured | Verdict |
|---|---|---|---|
| C1 — problem exists | positive adverse-selection cost on ≥ 70% of active pool-days | **126 / 126 (100.0%)** | **PASS** |
| C2 — loss is concentrated | top-decile 5-min risk windows explain ≥ 30% of loss | **80.4%** pooled (per-pool: M2 75.5%, M1 69.8%) | **PASS** |
| C3 — pain is predictable | pre-swap score vs ex-post cost: \|ρ\| ≥ 0.15, CI excludes 0 | **ρ = 0.0236**, 95% CI [0.0131, 0.0336] | **FAIL** |

Bootstrap: cluster = pool-day, B = 10,000, seed 20260823 (prereg §6). Per-role
ρ: M2 +0.0236, M1 −0.0267. Full statistics:
[`gate1-validation-results.json`](../evidence/gate1/gate1-validation-results.json);
committable derivatives: `evidence/gate1/derived/` (window-loss table +
seeded 2% trade sample, both SHA-256-pinned in the results file).

### What this establishes

- **The problem is real and severe.** On every single active sampled day,
  liquidity providers on the flagship native-ETH/USDC pool and on USDC/HYPE
  paid more in adverse selection than they recovered — 126 days out of 126.
  Five-minute risk windows are heavily concentrated: the worst decile carries
  roughly three-quarters of all losses. This is direct, chain-verifiable
  confirmation that v4 LPs on Unichain's busiest pools bear persistent,
  concentrated toxic-flow costs.
- **Our signal did not predict it.** A pre-trade composite of volatility
  (30 s EWMA), signed flow pressure (300 s), and pool-vs-reference deviation —
  equal-weighted because the v4-era training split is empty (prereg §6
  amendment) — ranks trades barely better than chance with respect to realized
  60-second adverse-selection cost (ρ ≈ 0.02).

### Why we think C3 failed (stated as interpretation, not finding)

Single-trade outcomes at a 60-second horizon are dominated by reference-price
noise; even an informative signal can drown at trade-level rank correlation.
Window-level or regime-level evaluation might behave differently. But those
are *different experiments*: the honest reading of the registered experiment is
that the five-signal dynamic-fee thesis, as specified, did not clear its own
bar. Resurrecting it with looser aggregation would be moving the goalposts;
any successor study must be a new pre-registration with new falsifiable bars
and fresh data boundaries.

### Consequences

Per the registered commitment: no Gate 2 economics study, no prototype
progression, no request for continued funding of the live-hook thesis on these
claims. HookGuard (risk transparency) and the V0 safety skeleton remain
standalone deliverables. The negative result, methodology, and all evidence
are published so that anyone — including us — cannot quietly re-run this study
until it passes.

### Reproduce

```bash
python3 -m research.sentinel_data.run_gate1_ingest \
  --config research/configs/unichain-core-cohort-v1.json \
  --plan evidence/gate1/measurement-plan-2025.json \
  --output-root evidence/gate1/windows-2025   # resumable; skips completed days

python3 -m research.sentinel_data.analyze_gate1 \
  --windows-root evidence/gate1/windows-2025 \
  --output evidence/gate1/gate1-validation-results.json

python3 -m unittest discover -s research/tests -v
```

## 2026-08-23 — Gate 1 preregistration checkpoint

The full Gate 1 design is now frozen in
[`GATE1_PREREG.md`](GATE1_PREREG.md) **before any measurement ran**: core
cohort, token metadata, reference-price rules, adverse-selection label,
feature windows, leakage controls, splits, seeds, and reporting commitment.

### What the nomination window showed

A 50,000-block Unichain mainnet window (`56,695,001`–`56,745,000`, ≈ 13.9 h)
produced a hash-receipted extraction of 46,457 events: 35,139 swaps, 11,316
liquidity changes, across 128 pool IDs. The frozen selection rule
(`sentinel-cohort-rule-v1`: rank by swap count, ≥ 500 minimum, resolved pair,
distinct unordered pairs, top-3 core / next-3 alternates) selected:

| Role | Pool | Pair | Swaps |
|---|---|---|---:|
| Core 1 | `0x75b1…e51d` | USDC / SOL | 8,257 |
| Core 2 | `0xc4f3…79c` | USDC / HYPE | 6,869 |
| Core 3 | `0x3258…d9` | ETH (native) / USDC | 5,144 |
| Alt 1–3 | `0x04b7…b16`, `0x51f9…96e`, `0x05db…771` | ETH/USD₮0, ETH/WBTC, WBTC/USD₮0 | 2,247 / 1,647 / 939 |

Token metadata (symbol, decimals) was probed via block-pinned `eth_call` at
the boundary hash and committed with raw results.

**What we learned (engineering):** public Unichain RPCs cap historical log
scans near a few thousand blocks per call, so (a) wide extractions must use
boundary-only block verification — added as an explicit manifest-recorded mode
rather than silently weakening the smoke receipts' full verification; and (b)
pool keys cannot be scanned historically or read from state cheaply, so cohort
currency pairs were resolved on-chain from ERC-20 transfer intersections in
single-pool swap transactions — a method that also identified native-ETH pairs
(the only v4 currency without a `Transfer` event). Both methods are frozen in
the prereg with their compensating controls.

**What this does not establish:** no reference prices were aligned, no labels
computed, no signal scored. Vanilla-pool status of each core pool is verified
at first study ingest (prereg §7); any violation swaps in the fixed alternate.

Receipts:
[`evidence/data-pipeline/unichain-mainnet-cohort-nomination-2026-08-23/`](../evidence/data-pipeline/unichain-mainnet-cohort-nomination-2026-08-23/)
(`events.jsonl` SHA-256
`cc092899e62da0a46854b92cd20068c1a43a031ef11927ce5f876c4cebfc4f87`) and
[`evidence/cohort/unichain-core-v1/`](../evidence/cohort/unichain-core-v1/)
(`selection_sha256`
`25f151e523717ad2e35aebe5582b4fcc0fbeb0ba3ff8c42f2ca1c2ca5fac8359`,
`metadata_sha256`
`7c60d7b94a15ae636497ed8fdf6d23617172f08481e06853ba57e9d00882de28`).

## 2026-08-23 — reproducible data-pipeline checkpoint

Sentinel can now ingest canonical `Initialize`, `Swap`, and `ModifyLiquidity`
events directly from a configured v4 `PoolManager`, normalize signed event
values, preserve large integers without precision loss, and publish an offline-
verifiable receipt containing exact block hashes and an events-file SHA-256.

### What the mainnet smoke window showed

The committed Unichain mainnet receipt covers blocks `56,732,741` through
`56,733,741`: a fixed 1,001-block / 1,000-second window.

| Observation | Measured result |
|---|---:|
| Total decoded PoolManager events | 302 |
| Swaps | 254 |
| Liquidity changes | 48 |
| Pool IDs with swaps | 30 |
| Pool IDs with liquidity changes | 4 |
| Total observed pool IDs | 31 |
| Blocks containing swaps | 155 |
| Distinct swap-fee values | 18 |

Activity was diverse but concentrated. The busiest pool produced 90 of 254
swaps (35.4%); the top three produced 158 (62.2%). Fee values `300` and `500`
together appeared in 186 of 254 swaps (73.2%), while the full raw fee field
ranged from `0` to `20,000` in this window.

**What we learned:** a short window can contain many pools and fee regimes while
still being dominated by a few markets. Gate 1 therefore needs a pre-frozen,
stratified cohort and pool-month reporting. A single aggregate or hand-picked
busy pool could materially overstate how general a signal is.

**What this does not establish:** this window is an ingestion smoke test, not a
representative market sample. Pool metadata was not joined, token units were
not converted, and no reference-price, LVR, adverse-selection, predictive-signal
or LP-economics calculation was performed.

Receipt:
[`evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23/`](../evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23/)
(`events.jsonl` SHA-256
`3386f9aae8ae4013342432f59b4e382900c809741c1003c593090d680655b141`).

### What the Sentinel demo receipt showed

The same decoder captured Sentinel's Unichain Sepolia demo from pool creation
through the fee ramp: 1 `Initialize`, 1 `ModifyLiquidity`, and 7 `Swap` events.
The raw swaps independently reproduce the onchain choreography:

| Swap | End tick | Fee paid |
|---:|---:|---:|
| Calm trade | -4 | 500 (0.05%) |
| Large move | -359 | 500 (0.05%) |
| Next trade | -355 | 1,000 (0.10%) |
| Next trade | -358 | 1,500 (0.15%) |
| Next trade | -355 | 2,000 (0.20%) |
| Next trade | -358 | 2,500 (0.25%) |
| Next trade | -355 | 3,000 (0.30%) |

**What we learned:** the large trade moved the pool 355 ticks but still paid the
base fee; only later trades paid the rate-limited increases. This is direct raw-
event evidence that V0 uses pre-swap state rather than letting a trade set the
fee it pays. It proves the implementation behavior, not that the rule improves
LP economics.

Receipt:
[`evidence/data-pipeline/unichain-sepolia-demo/`](../evidence/data-pipeline/unichain-sepolia-demo/)
(`events.jsonl` SHA-256
`8a941b5b39703b40e27930f2cd8e22f0c219043617b43107fe9e11e0ddeb1615`).

### Engineering finding: public RPCs are not interchangeable

Unichain Sepolia accepted batched JSON-RPC block lookups; the public Unichain
mainnet endpoint returned a non-batch response. The extractor now falls back to
sequential block receipts while preserving the same hash checks. This makes the
pipeline portable without silently weakening evidence integrity or requiring a
specific paid provider.

### Decisions locked by this checkpoint

- Raw `PoolManager` events remain the primary ingestion source; subgraphs and
  standardized datasets are cross-checks, not the sole source of truth.
- Every published sample uses fixed block bounds, boundary block hashes, event
  counts, observed pool IDs, and an exact file hash.
- Signed v4 swap amounts remain signed in the normalized output. Large numeric
  fields remain decimal strings to prevent downstream precision loss.
- Pools whose hooks return swap deltas require hook-aware accounting; default
  `Swap` amounts will not be treated as universally complete.
- Testnet behavior evidence and mainnet economic evidence remain explicitly
  separate.

## Next public checkpoint — Gate 1 measurement

The six preregistration items previously listed here are now **frozen** in
[`GATE1_PREREG.md`](GATE1_PREREG.md) (2026-08-23). The next checkpoint is the
Gate 1 measurement itself: ingest the study windows for the three core pools,
align on-chain reference prices per the frozen rules, compute labels and
pre-swap features, and publish the pass/fail result — including a failure.

Until that report exists, Sentinel makes **no claim** that current LP pain is
sufficiently predictable, that the five-signal model works, or that dynamic
fees improve LP returns.

## Reproduce or verify

Commands and data-source references are in the
[`research` README](README.md). The committed receipts can be verified without
network access:

```bash
python3 -m research.sentinel_data.verify \
  evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23 \
  evidence/data-pipeline/unichain-sepolia-demo
```
