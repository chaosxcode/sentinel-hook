# Sentinel research findings

**Last updated:** 2026-08-23  
**Current stage:** raw Uniswap v4 event ingestion  
**Gate status:** Gate 1 has not been evaluated

This is a dated, evidence-linked research log for grant reviewers and other
builders following Sentinel's progress. Findings are separated from hypotheses
so implementation progress cannot be mistaken for economic validation.

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

## Next public checkpoint — Gate 1 preregistration

Before running the larger study, Sentinel will publish and freeze:

1. At least three named core pools plus inclusion/exclusion rules and a transfer
   rule for smaller Unichain pilot markets.
2. Token metadata and unit normalization with source and block provenance.
3. External reference-price venue(s), timestamp alignment, stale-price rules,
   and missing-data handling.
4. The exact adverse-selection/LVR label and pre-swap feature windows.
5. Leakage controls, parameter-search policy, seeds, exclusions, and the
   train/validation/locked-holdout split.
6. A Gate 1 report that publishes the result even if the thesis fails.

Until those items are frozen and run, Sentinel makes **no claim** that current
LP pain is sufficiently predictable, that the five-signal model works, or that
dynamic fees improve LP returns.

## Reproduce or verify

Commands and data-source references are in the
[`research` README](README.md). The committed receipts can be verified without
network access:

```bash
python3 -m research.sentinel_data.verify \
  evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23 \
  evidence/data-pipeline/unichain-sepolia-demo
```
