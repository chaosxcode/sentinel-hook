# Sentinel research findings

**Last updated:** 2026-08-23  
**Current stage:** Gate 1 preregistration frozen  
**Gate status:** Gate 1 has not been evaluated

This is a dated, evidence-linked research log for grant reviewers and other
builders following Sentinel's progress. Findings are separated from hypotheses
so implementation progress cannot be mistaken for economic validation.

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
