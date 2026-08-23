# Sentinel — Gate 1 preregistration

**Frozen:** 2026-08-23  
**Status:** preregistered, measurement not yet run  
**Rule of the road:** this document freezes the cohort, definitions, splits,
and decision criteria *before* any Gate 1 measurement is produced. Nothing in
this document claims the adverse-selection thesis is true.

Gate 1 asks one question: **is LP pain on core Unichain v4 markets strong and
predictable enough to justify building a dynamic-fee hook?** The pass/fail bars
below are copied unchanged from the [grant application](https://chaosxcode.github.io/sentinel-hook/)
so they cannot drift after results are known.

---

## 1. Frozen inputs

Every artifact below is committed to this repository and hash-receipted.

| Artifact | Path | Integrity |
|---|---|---|
| Nomination-window event extraction | `evidence/data-pipeline/unichain-mainnet-cohort-nomination-2026-08-23/` | `events.jsonl` SHA-256 `cc092899e62da0a46854b92cd20068c1a43a031ef11927ce5f876c4cebfc4f87` |
| Cohort-selection rule output | `evidence/cohort/unichain-core-v1/cohort.json` | `selection_sha256` `25f151e523717ad2e35aebe5582b4fcc0fbeb0ba3ff8c42f2ca1c2ca5fac8359` |
| Token metadata probes | `evidence/cohort/unichain-core-v1/tokens.json` | `metadata_sha256` `7c60d7b94a15ae636497ed8fdf6d23617172f08481e06853ba57e9d00882de28` |

Nomination window: Unichain mainnet (chain 130), PoolManager
`0x1F98400000000000000000000000000000000004`, blocks **56,695,001 – 56,745,000**
(exactly 50,000 blocks ≈ 13.9 hours), boundary hashes pinned in the manifest.
46,457 events: 35,139 swaps, 11,316 liquidity changes, 2 initializations,
across 128 distinct pool IDs.

## 2. Cohort (prereg item 1)

Selection was performed by `research/sentinel_data/select_cohort.py` under the
frozen rule `sentinel-cohort-rule-v1`, embedded verbatim in that module and in
the selection receipt: rank all pools by in-window swap count (ties broken by
ascending pool ID); require ≥ 500 swaps; require a resolved currency pair;
require pairwise-distinct unordered currency pairs; take the first 3 as core
and the next 3 as alternates.

### Core pools

| # | Pool ID | Pair | Swaps in window |
|---|---|---|---:|
| 1 | `0x75b19237e8069600566be945953c5fd815cdfb26bd3972821d0ae8d6b7d5e51d` | USDC / SOL | 8,257 |
| 2 | `0xc4f393785b36430779a93eedd52dd20857a46142bbe48c88d4c655303a53279c` | USDC / HYPE | 6,869 |
| 3 | `0x3258f413c7a88cda2fa8709a589d221a80f6574f63df5a5b6774485d8acc39d9` | ETH (native) / USDC | 5,144 |

### Alternate pools (substitution order fixed)

| # | Pool ID | Pair | Swaps in window |
|---|---|---|---:|
| A1 | `0x04b7dd024db64cfbe325191c818266e4776918cd9eaf021c26949a859e654b16` | ETH (native) / USD₮0 | 2,247 |
| A2 | `0x51f9d63dda41107d6513047f7ed18133346ce4f3f4c4faf899151d8939b3496e` | ETH (native) / WBTC | 1,647 |
| A3 | `0x05dbb214bd7b9461f9c2f6690b612629b65b9f81d7312fdd3e552d2dda85f771` | WBTC / USD₮0 | 939 |

### Inclusion / exclusion rules

- Only canonical v4 `PoolManager` events on chain 130 count as evidence.
- A pool enters the ranking only through observed activity; no pool may be
  added by judgment after results are seen.
- Pools are excluded from candidacy when their currency pair cannot be
  resolved by the frozen procedure, or when their unordered pair duplicates an
  already-selected pool.
- The full ranked top-15 and every exclusion reason are recorded in the
  selection receipt.

### Transfer rule for smaller Unichain pilot markets

Findings may generalize beyond the core cohort only via this rule: any
additional pool brought into a pilot must satisfy (a) the same eligibility
rule applied to a freshly extracted, hash-receipted window, (b) vanilla-pool
verification once its `Initialize` event is ingested (see §7), and (c) it must
be reported as a separate pool-row — never pooled into core-pool aggregates.

## 3. Token metadata and unit normalization (prereg item 2)

Metadata for all six cohort currencies was read via `eth_call` pinned to the
nomination window's boundary block hash
(`0x3b3438dfcfd9e8fb2349ac3275ad54e9fab0a0acbc94dd101254e02f8a9f26bb`, block
56,745,000) and committed with raw call results (`tokens.json`). Decimals used
for normalization:

| Currency | Address | Decimals |
|---|---|---:|
| USDC | `0x078d782b760474a361dda0af3839290b0ef57ad6` | 6 |
| SOL | `0xbde8a5331e8ac4831cf8ea9e42e229219eafab97` | 9 |
| HYPE | `0x15d0e0c55a3e7ee67152ad7e89acf164253ff68d` | 18 |
| ETH (native) | `0x0000000000000000000000000000000000000000` | 18 |
| USD₮0 | `0x9151434b16b9763660705744891fa906f660ecc5` | 6 |
| WBTC | `0x0555e30da8f98308edb960aa94c0db47230d2b9c` | 8 |

- All human-scale amounts are raw units divided by `10^decimals`; raw decimal
  strings are never rounded during computation.
- Notional values use the reference price at the swap's aligned timestamp
  (§4) times normalized amount.
- If any selected pool's token later proves non-standard (reverting metadata
  calls, inconsistent transfers), the pool is replaced by the next alternate
  in fixed order and the substitution is reported in the Gate 1 report.

## 4. Reference prices and alignment (prereg item 3)

**Amendment to the grant text.** The grant application listed high-frequency
CEX archives as the reference-price source. That venue is amended here to
**on-chain Unichain venues**, because it keeps every input verifiable from
public chain data without API keys or vendor terms. Consequence accepted:
reference prices share market microstructure with the studied pools, which can
understate cross-venue adverse selection. This limitation is carried into the
Gate 1 report template.

- Venue-of-record: the union of Unichain mainnet v4 pools trading the same
  currency pair as the study pool, **excluding the study pool itself**, plus
  the canonical deep pools for the quote asset (ETH/USDC, ETH/USD₮0). The
  exact venue set per study pool is fixed at measurement start from the first
  ingestion window and published before labels are computed.
- Reference price series: time-weighted midprice from `Swap` events of venue
  pools, computed from post-swap `sqrtPriceX96` converted to a normalized
  price, weighted by pool depth (`liquidity` at the event tick).
- Timestamp source: block timestamps from chain data (boundary-pinned in
  receipts). Unichain targets 1-second blocks; alignment uses timestamps, not
  block arithmetic.
- Alignment rule: every study-pool swap is matched with the **last reference
  observation strictly before** its block timestamp. No look-ahead: the
  reference value at time t uses information available at or before t.
- Stale-price rule: if the newest available reference observation is older
  than 60 seconds, the swap's label is marked `stale_reference` and excluded
  from label statistics while remaining in feature statistics. The fraction of
  stale-labeled swaps is reported per pool-month.
- Missing-data rule: if a reference venue has a gap longer than 10 minutes,
  the affected pool-day is flagged and excluded from "active day" counts;
  exclusions are reported, never silently interpolated.

## 5. Adverse-selection label and feature windows (prereg item 4)

**Label (per swap, per horizon).** Let `p_ref(t)` be the reference price. For
swap i executed at time t_i with direction `d_i ∈ {−1,+1}` (sell/buy inferred
from `amount0` sign) and normalized size `q_i` priced at notional `n_i`:

```
move(i,h) = d_i × ( p_ref(t_i + h) − p_ref(t_i) ) / p_ref(t_i)
as_cost(i,h) = n_i × move(i,h)
```

Horizons frozen: `h ∈ {1 block, 60 s}`. Positive `as_cost` means the trader
was right against LPs — adverse selection. Per-window aggregation sums
`as_cost` across swaps in the window; pool-day aggregates are sums over UTC
days with ≥ 100 labeled swaps ("active days").

**Features (pre-swap only).** Computed exclusively from study-pool events with
timestamps `< t_i`:

1. Volatility: EWMA of squared 1-s reference returns, half-lives {30 s, 300 s}.
2. Size pressure: rolling signed flow imbalance over {30 s, 300 s}, normalized
   by rolling total notional.
3. Deviation: `(p_pool(t) − p_ref(t)) / p_ref(t)` using the pool's own last
   pre-swap midprice.
4. Imbalance: book-state proxy from pool `liquidity` and current tick distance
   to nearest initialized range edges.
5. Acceleration: first difference of the 30 s volatility EWMA over the last
   10 s.

The five-signal score is a fixed-weight linear combination during Gate 1
(weights are the searched parameter, §6). No feature may use data at or after
`t_i`; this is enforced in code by constructing features from shifted series
only, and audited by re-computing features for a random sample of swaps from
raw receipts.

**Gate 1 pass criteria (unchanged from the grant):**

- Positive pooled adverse-selection cost on ≥ 70% of active study days.
- Top-decile risk windows explain ≥ 30% of total measured loss.
- Pre-swap score vs ex-post `as_cost` Spearman |ρ| ≥ 0.15 on validation data,
  distinguishable from zero under the clustered bootstrap below.

Failure of any criterion = Gate 1 failed; methodology and negative results are
published and the live-hook thesis terminates (per the grant).

## 6. Leakage controls, search policy, seeds, exclusions, splits (prereg item 5)

**Splits (calendar-based, from the grant):**

| Split | Period | Use |
|---|---|---|
| Train | Jan 2023 – Dec 2024 (v3-era data where v4 is unavailable) | signal exploration, weight fitting |
| Validation | Jan – Dec 2025 (v3 + live v4) | exactly-once model selection |
| Locked holdout | Jan – Jul 2026 | evaluated exactly once, after parameter freeze |

Unichain-mainnet v4 history begins after these calendar starts; where a period
has no chain data for a pool, that pool contributes zero rows to the split and
the gap is reported. Calendar boundaries are never moved to accommodate data
availability.

**Leakage controls:**

- Features strictly pre-swap (§5); labels strictly post-swap horizons.
- Reference venues exclude the study pool (§4).
- Normalizations (volatility scales, notional quantiles) fit on train only.
- No parameter, threshold, exclusion, or window may change after validation
  scores exist; every such value is either frozen above or fitted on train.

**Search policy:** the five weights and the elevated-tier trigger threshold
are searched on a fixed grid (weights ∈ {0, ±0.25, ±0.5, ±1} per signal,
threshold ∈ {50th, 75th, 90th percentile}) scored on train by pooled |ρ|,
then the top-10 configurations are scored on validation exactly once. Best
validation configuration is final. Seeds: all randomness (bootstrap, tie
breaking, subsampling) uses seed `20260823`.

**Statistical testing:** clustered bootstrap over pool-days, B = 10,000
resamples, two-sided α = 0.05; clusters preserve within-day dependence.

**Exclusions (all reported):** swaps with `stale_reference` labels; pool-days
with < 100 labeled swaps; same-transaction multi-swaps (suspected atomic
arbitrage) are labeled and reported separately rather than deleted;
pool-months affected by reference-venue outages.

## 7. Deferred verification obligations

Two facts cannot be verified from the nomination window on public
infrastructure today, and the prereg binds them to the study's first ingest
instead of pretending they are known:

1. **Vanilla-pool status.** Each core pool's `Initialize` event (fee tier,
   tick spacing, hooks address) must be located and verified during the first
   study ingestion window, which necessarily contains it. If any core pool has
   a non-zero hooks address, it is replaced by the next alternate in fixed
   order, and the replacement is reported. Rationale: hooked pools confound
   fee behavior and hook-aware accounting is explicitly out of scope for Gate
   1 (locked decision, FINDINGS 2026-08-23).
2. **Pair-resolution caveat.** Currency pairs were resolved from ERC-20
   transfer intersections in single-pool swap transactions (method frozen in
   the selection receipt). A deeply non-standard token that skips `Transfer`
   emissions could in principle mislabel a native-side pair; the §3 metadata
   probes and the §7 Initialize verification are the compensating controls.

## 8. Reporting commitment (prereg item 6)

The Gate 1 report will be published in `research/FINDINGS.md` with: per-pool
active-day fractions, loss concentration, validation correlations with
confidence intervals, every exclusion count, the exact configuration tested,
and — if Gate 1 fails — the explicit statement that the thesis is terminated.
No result will be described as preliminary-in-passing: Gate 1 either passes
its pre-registered bars or fails them.

## 9. Reproduction

```bash
python3 -m research.sentinel_data.extract \
  --config research/configs/unichain-mainnet-cohort-nomination-2026-08-23.json \
  --output-dir evidence/data-pipeline/unichain-mainnet-cohort-nomination-2026-08-23

python3 -m research.sentinel_data.select_cohort \
  --config research/configs/unichain-core-cohort-v1.json \
  --output evidence/cohort/unichain-core-v1/cohort.json

python3 -m research.sentinel_data.token_metadata \
  --config research/configs/unichain-core-cohort-v1.json \
  --cohort evidence/cohort/unichain-core-v1/cohort.json \
  --output evidence/cohort/unichain-core-v1/tokens.json

python3 -m unittest discover -s research/tests -v
```
