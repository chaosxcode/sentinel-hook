# GitHub Discussion launch post — Sentinel

**Suggested title:**
```
Sentinel: a Uniswap v4 hook that prices adverse selection — failed its first test publicly, then passed the holdout. Full research arc + interactive lab inside.
```

**Suggested category:** Show and tell

**How to post when ready:**
```
gh api graphql -f query='mutation { createDiscussion(input: {repositoryId: "<REPO_NODE_ID>", categoryId: "<CATEGORY_ID>", title: "<TITLE>", body: <BODY>}) { discussion { url } } }'
```
(or paste `discussion-body.md` content into the web form)

---

## body.md

---

### Sentinel: a v4 hook that prices adverse selection — the full research arc, fail included

![Sentinel v1 fee curve — measured on-chain](https://raw.githubusercontent.com/chaosxcode/sentinel-hook/main/docs/social/fee-ramp.gif)

**TL;DR:** We built a Uniswap v4 hook that keeps LP fees at 0.05% in calm
markets and raises them continuously toward 1% when informed flow is bleeding
liquidity providers. Our first signal **failed its pre-registered test** — we
published the failure — the post-mortem showed us where the signal actually
lives, and the rebuilt policy **passed a locked 7-month holdout evaluation on
all three pre-registered bars**. Live on Unichain Sepolia at 14k gas. Play
with it: **[Sentinel Lab](https://chaosxcode.github.io/sentinel-hook/lab.html)**.

---

**The problem we measured first.** Before building anything, we ran a
pre-registered study on 123 sampled days of Unichain mainnet: 2,536,933
labeled trades across the deepest native-ETH/USDC v4 pool. Liquidity providers
paid adverse-selection costs on **126 of 126 active days**. The worst 10% of
five-minute windows carried **~80% of all losses**. If you LP on volatile
pairs, you're selling insurance at a flat price against a spiky risk.

**The honest failure.** Our first signal — the thing that was supposed to
predict which trades hurt LPs — scored ρ ≈ 0.02 against a pre-registered bar
of 0.15. Dead. We published the failure exactly as committed, with seeds,
exclusions, and methodology, because a research program that only publishes
wins is marketing.

**What the data actually showed.** Zoom out from trades to time windows and
the signal appears: trailing losses predict next-window losses at ρ = 0.61.
Toxic flow *clusters*. A fee doesn't need to predict individual trades — it
needs to know when to be high. So v2 is a continuous policy:
`fee = clamp(4 × EMA[realized 60s price move], 0.05%, 1.00%)` — no oracle, no
keeper, computed entirely from the hook's own state.

**The holdout.** Seven months of data the policy never saw (Jan–Jul 2026,
1,243,673 labeled trades), evaluated exactly once under a protocol frozen
before ingestion:

| Bar | Result |
|---|---|
| Net LP improvement vs static | **+$366,054**, bootstrap 95% CI [7.7, 12.6] bps — excludes zero |
| Robustness | positive in **8 of 8** pool-months, both pools |
| Trader burden | **9.5 bps**, inside the pre-committed 12 bps bound |

**Security.** 100,000-step stateful fuzz campaign, zero invariant violations.
We also attacked our own signal and published the results: wait-out dodges
work once (disclosed), volatility poisoning costs the attacker 3.29
units/3min with zero revenue, and split-trading a large order is *not*
cheaper. Dossier: [docs/SECURITY.md](https://github.com/chaosxcode/sentinel-hook/blob/main/docs/SECURITY.md).

**Try to break it.** The [Lab](https://chaosxcode.github.io/sentinel-hook/lab.html)
lets you replay 25k real labeled trades through the fee engine with your own
parameters, and polls our deployed testnet contract live. In our sweeps, 18/18
configurations beat static — if you find ones that don't, that's research:
post your sliders.

**Links:**
- Research log (failure included): [research/FINDINGS.md](https://github.com/chaosxcode/sentinel-hook/blob/main/research/FINDINGS.md)
- Protocols: [Gate 1](https://github.com/chaosxcode/sentinel-hook/blob/main/research/GATE1_PREREG.md) · [V2 holdout](https://github.com/chaosxcode/sentinel-hook/blob/main/research/V2_PREREG.md)
- Contract: [SentinelHookV1.sol](https://github.com/chaosxcode/sentinel-hook/blob/main/src/SentinelHookV1.sol) on [Unichain Sepolia](https://sepolia.uniscan.xyz/address/0x290d2d0af6dd11b6e235eac6d7528f5474753080)
- Visual findings: [chaosxcode.github.io/sentinel-hook/findings.html](https://chaosxcode.github.io/sentinel-hook/findings.html)

Feedback welcome — especially on the holdout methodology and the pilot design
([research/PILOT_DESIGN.md](https://github.com/chaosxcode/sentinel-hook/blob/main/research/PILOT_DESIGN.md)).
Mainnet pilot is frozen and waiting on an independent security review.
