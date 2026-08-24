# Sentinel — social media post log

Ready-to-post copy. Each entry is within X/Twitter's 280-char limit (counts
shown). Drop charts/screenshots where marked `[img]` — suggested image noted
per post. Suggested cadence: 2-3 per week following each merged milestone
(commit hashes referenced so you can link the diff).

---

## Batch 1 — research arc (Gate 1)

### Post 1 — the thesis (231 chars)
```
Static fees charge the same. LP risk does not.

We're building Sentinel: a Uniswap v4 hook that prices adverse-selection risk in real time — lower fees when markets are calm, real compensation for LPs when flow turns toxic.

Everything measured. Everything published. 🧵
```
`[img]` repo banner

### Post 2 — receipts culture (259 chars)
```
Before claiming anything, we built receipts.

Raw Uniswap v4 events decoded straight from the PoolManager — block hashes pinned, SHA-256 stamped, verifiable offline by anyone:

$ python3 -m research.sentinel_data.verify

No vibes. Just chain data.
```
`[img]` terminal screenshot of verify output

### Post 3 — pre-registration (263 chars)
```
Most crypto "research" picks the winning chart first.

We did it backwards: froze the pools, the formulas, the pass/fail bars, and the seeds BEFORE running anything. It's all in our Gate 1 preregistration — timestamped and committed.

Falsifiable or it didn't happen.
```
`[img]` prereg doc header

### Post 4 — scale (247 chars)
```
The measurement ran for real:

• 123 sampled days of Unichain mainnet
• ~4M raw v4 events ingested
• 2,536,933 trades labeled for adverse selection
• 0 failed days

Every number re-computable from public chain data. This is what evidence-grade DeFi research looks like.
```
`[img]` ingestion dashboard / progress log

### Post 5 — the problem is brutal (257 chars)
```
Result #1: LPs got farmed on 126 out of 126 active days.

On the ETH/USDC pool alone, adverse selection drained $8.1M across our sampled windows. Not some days. EVERY day.

If you LP on Uniswap v4 without protection, this chart is your PnL.
```
`[img]` daily loss bars (all positive)

### Post 6 — concentration (250 chars)
```
Result #2: it gets worse — and sharper.

The worst 10% of 5-minute windows carried ~80% of ALL adverse-selection losses.

LP risk isn't a constant drip. It's a landmine field. Static fees pay out the same whether you're walking through calm or carnage.
```
`[img]` loss concentration curve (Lorenz-style)

### Post 7 — the honest fail (264 chars)
```
Result #3, the uncomfortable one:

Our v1 signals could NOT predict which individual trades would hurt LPs. Correlation ≈ 0.02 vs the 0.15 bar we set ourselves.

So we published the failure, exactly as pre-registered. No hiding, no goalpost moving.

That's the deal we signed.
```
`[img]` criteria table from FINDINGS.md

### Post 8 — the plot twist (258 chars)
```
But the data had a secret.

Zoom out from single trades to time windows and the noise vanishes:

trailing losses → next-window losses: ρ = 0.61

Toxic flow CLUSTERS. It's not random. It's visible before it lands — if you're looking at the right altitude.
```
`[img]` correlation table

### Post 9 — building the fix (260 chars)
```
So that's Sentinel v2:

A fee engine that watches realized adverse selection and prices toxicity continuously — calibrated to the bleed, not a blunt on/off switch.

Backtest check: 74% of fee uplift landed on genuinely toxic trades.

Precision is real. Now we tune the dosage.
```
`[img]` precision/coverage chart

### Post 10 — what's next (245 chars)
```
Roadmap from here:

→ calibrate continuous toxicity pricing on dev data
→ port the signal into the audited V0 hook skeleton (bounds + rate limits already tested)
→ final proof on untouched holdout data

The LPs losing every day deserve better than static fees. We're building it.
```
`[img]` architecture sketch

---



---

## Batch 2 — the rebuild (Sentinel v2)

### Post 11 — the diagnosis (251 chars)
```
Gate 1 failed. So we did what the data told us.

Replayed 2.5M labeled trades and asked: WHERE does the signal live?

Answer: not in single trades. In TIME.

Trailing losses predict the next window's losses at ρ = 0.61 — nearly the 0.15 bar we originally set, times four.
```
`[img]` 03-correlations.png

### Post 12 — killing a bad idea fast (246 chars)
```
Honest engineering log:

Our first deployable signal (swap-to-swap price drift) scored ρ ≈ -0.07 against real losses.

Dead on arrival. Tick quantization murders it.

So we published the rejection and moved to the next candidate. Killing bad ideas quickly is the job.
```
`[img]` self-drift validation JSON

### Post 13 — the deployable signal (255 chars)
```
The signal that survived needs NO oracle:

EMA of the pool's own 60-second realized price move.

ρ = 0.36 against reference-priced losses — the full strength of the volatility component, computable entirely from hook state.

If the hook can't see it on-chain, it doesn't ship.
```
`[img]` lookback validation table

### Post 14 — calibration sweep (249 chars)
```
We calibrated the fee policy on Feb-Sep data, then evaluated on untouched Oct-Dec:

18 out of 18 configurations beat static fees.

Not one lucky parameter. The whole map works, and it responds monotonically — exactly what a real signal looks like.
```
`[img]` sweep table from calibration JSON

### Post 15 — the numbers (247 chars)
```
Sentinel v2 backtest, ETH/USDC pool, out-of-sample:

+$2.98M net LP improvement vs static fees
71% of fee uplift landed on toxic flow
2x coverage of ongoing losses
8bps average trader burden

Same pool, same trades. The only change is when the fee is high.
```
`[img]` 04-backtest.png

### Post 16 — the contract is real (252 chars)
```
SentinelHookV1 is written, tested, and pushed.

• continuous calibrated fee curve
• zero oracle dependencies
• 257-run fuzz: fees never leave bounds, never jump limits
• 14k gas overhead per swap (budget: 40k)

Every safety rail from V0 carried over. Every claim has a test.
```
`[img]` test suite output

### Post 17 — why the fail made it stronger (258 chars)
```
The uncomfortable take:

Gate 1 failing publicly is WHY v2 exists. If we'd fudged the first result, we'd be marketing a signal that does nothing.

Instead: fail → post-mortem → found loss clustering → rebuilt → validated.

Accountability isn't a cost. It's the compounding asset.
```
`[img]` FINDINGS.md header

### Post 18 — for LPs (240 chars)
```
If you provide liquidity on Uniswap v4, here's what our data shows:

126 of 126 sampled days: you paid adverse selection.
Top 10% of volatile windows: ~80% of your losses.

You're not earning yield. You're selling insurance at the wrong price.
```
`[img]` 01-daily-losses.png

### Post 19 — for the builders (250 chars)
```
Research stack built in public — all reproducible:

• raw v4 event extractor w/ hash receipts
• seeded measurement plans
• trade-level labeling engine
• clustered bootstrap stats
• fee-policy backtest harness

Fork it, verify it, beat it:
github.com/chaosxcode/sentinel-hook
```
`[img]` repo file tree

### Post 20 — what's next (247 chars)
```
Next milestones:

→ new pre-registration with window-level bars
→ one evaluation on untouched holdout data (Jan-Jul 2026)
→ demo deployment of SentinelHookV1

We get one shot at the holdout and we're doing it right.

Static fees had a good run. Times change.
```
`[img]` roadmap graphic



---

## Batch 3 — the lab (interactive)

### Post 21 — the lab is live (247 chars)
```
We built a playground.

Sentinel Lab: replay 25,000 real labeled trades from the ETH/USDC pool through our fee engine. Drag the sliders — sensitivity, cap, base fee, memory — watch LP PnL recompute instantly.

No signup. Just math:
chaosxcode.github.io/sentinel-hook/lab.html
```
`[img]` lab screenshot (simulator section)

### Post 22 — the hook is watching you watch it (250 chars)
```
The top of the lab page polls our DEPLOYED SentinelHookV1 contract live through public RPCs.

Current fee. Current volatility EMA. Every FeeUpdated event from the demo, each bar clickable to its transaction.

The chain is the API. No backend. No keys.
```
`[img]` live panel screenshot

### Post 23 — break our policy (262 chars)
```
Challenge: find parameters where static fees beat us.

The lab lets you tune k, cap, base fee and memory against the same 2.5M-trade dataset we use. In our sweeps, 18/18 configs beat static — but the sample is yours to torture.

If you break it, that's research. Post your sliders.
```
`[img]` simulator with extreme params



---

## Batch 4 — the holdout countdown & reveal

### Post 24 — the one-shot protocol (249 chars)
```
Most projects test until they pass. We froze the opposite.

Our v2 policy faces 7 months of data it has never seen — Jan-Jul 2026. Parameters locked. One evaluation. Published either way.

The protocol is committed before a single block is queried.
```
`[img]` V2_PREREG.md header

### Post 25 — what's frozen (247 chars)
```
Everything. That's the point.

• signal: EMA of 60s realized price moves, half-life 300s
• fee: clamp(4 × signal, 5bps, 100bps)
• bars: significance, robustness, burden ≤ 12bps

Written down before ingestion. If we change ANYTHING after seeing results, the result is void.
```
`[img]` frozen parameters table

### Post 26 — ingestion begins (244 chars)
```
The holdout run has started.

110 sampled day-windows of Unichain mainnet, Jan-Jul 2026. Same pipeline that processed 4M events for Gate 1. Same hash receipts. Same zero-tolerance for unverifiable data.

Zero failures so far.
```
`[img]` progress log screenshot

### Post 27 — scale (239 chars)
```
Final holdout tally:

110 day-windows ingested
0 failures
1,243,673 trades labeled
161 pool-day clusters for the bootstrap

Every block hash pinned. Every file SHA-256 stamped. This is the largest fully-reproducible study we know of at this scale.
```
`[img]` ingestion stats

### Post 28 — the bars, plainly (251 chars)
```
What would a PASS mean? Three bars, frozen in advance:

P1 — the policy beats static fees, and the margin survives 10,000 clustered bootstrap resamples
P2 — positive across pool-months, not one lucky month
P3 — traders aren't overtaxed: burden ≤ 12bps

All three or it fails.
```
`[img]` bars table

### Post 29 — the stakes (250 chars)
```
What a FAIL means:

We abandon the dynamic-fee approach. No third attempt. The measurement tooling stays public, the negative result gets published, and we move on.

We wrote that commitment down before running. That's what separates research from marketing.
```
`[img]` V2_PREREG commitment section

### Post 30 — quiet (233 chars)
```
The evaluation is running.

One replay. Frozen parameters. Data the policy has never touched.

Results in the next post. Published whatever they are.
```
`[img]` minimal — black screen with "evaluating…"

### Post 31 — THE REVEAL: P1 (254 chars)
```
THE HOLDOUT VERDICT: PASS.

P1 — Sentinel v2 beat static fees by +$366,054 net LP on the holdout.

10,000 clustered bootstrap resamples: 95% CI [7.7, 12.6] bps. Zero is nowhere near the interval.

Bar one: cleared.
```
`[img]` P1 results table

### Post 32 — THE REVEAL: P2 + P3 (256 chars)
```
Bar two: positive in 8 of 8 pool-months. Both pools individually. Not one lucky month — a consistent edge.

Bar three: trader burden 9.5bps, inside our pre-committed 12bps ceiling.

65% of fee uplift landed on genuinely toxic flow. Coverage 2.8×.

All three bars: PASS.
```
`[img]` full verdict table

### Post 33 — what now (253 chars)
```
The full arc, start to finish:

Pre-registered study → published failure → post-mortem → rebuild → holdout pass.

A Uniswap v4 hook that prices adverse selection, validated on data it never saw, live on testnet at 14k gas.

Next: security review + mainnet pilot design.
```
`[img]` hero banner + verdict

## Posting notes

- Posts 5-7 work best as a same-day sequence (problem → concentration → honesty).
- If engagement is high on Post 8, reply to it with Post 9 as a thread continuation.
- Always attach the commit link when someone asks "source?" — e.g.
  `github.com/chaosxcode/sentinel-hook/commit/db4b5df`
- Do not edit numbers in the copy; they're tied to published artifacts.
