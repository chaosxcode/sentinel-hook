# Sentinel research pipeline

This directory implements the repo's research milestones: reproducible raw-
data ingestion first, then frozen study design, then Gate 1 measurement.

**Current measured findings and next checkpoint:**
[`FINDINGS.md`](FINDINGS.md) (last updated 2026-08-23).

**Gate 1 preregistration (frozen 2026-08-23):**
[`GATE1_PREREG.md`](GATE1_PREREG.md) — cohort, labels, splits, and decision
bars fixed before measurement.

The extractor reads canonical `Initialize`, `Swap`, and `ModifyLiquidity`
events from a configured Uniswap v4 `PoolManager`, validates the chain and
block hashes, normalizes signed values, and writes:

- `events.jsonl` — canonical chain-ordered records; large integers are decimal
  strings to prevent precision loss.
- `manifest.json` — chain/range receipts, event and pool counts, limitations,
  and the SHA-256 of the exact event file.

It uses Python's standard library only. A private RPC can be supplied through
the environment variable named by each config; the URL is never copied into
the manifest. Otherwise the documented public Unichain endpoint is used.

## Reproduce the committed receipts

```bash
python3 -m research.sentinel_data.extract \
  --config research/configs/unichain-mainnet-smoke-2026-08-23.json \
  --output-dir evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23

python3 -m research.sentinel_data.extract \
  --config research/configs/unichain-sepolia-demo.json \
  --output-dir evidence/data-pipeline/unichain-sepolia-demo

python3 -m research.sentinel_data.verify \
  evidence/data-pipeline/unichain-mainnet-smoke-2026-08-23 \
  evidence/data-pipeline/unichain-sepolia-demo
```

## Reproduce the Gate 1 cohort freeze

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
```

The selection rule (`sentinel-cohort-rule-v1`) is embedded in
`sentinel_data/select_cohort.py` and mirrored in the cohort receipt; the
preregistration narrative is [`GATE1_PREREG.md`](GATE1_PREREG.md).

## What this does not prove

These receipts prove that Sentinel can reproducibly ingest and decode raw v4
events, and — together with the cohort freeze — that the Gate 1 study design
was fixed before measurement. They do **not** pass Gate 1: no reference price
has been aligned, no adverse-selection label computed, and no signal scored.
The preregistration's deferred-verification obligations (vanilla-pool checks,
pair-resolution caveats) are listed in [`GATE1_PREREG.md`](GATE1_PREREG.md) §7.

The event conventions follow the Uniswap Foundation's
[v4 data guide](https://www.uniswapfoundation.org/blog/how-to-navigate-uniswap-v4-data).
The configured PoolManager addresses and deployment blocks come from the
[official v4 subgraph network registry](https://github.com/Uniswap/v4-subgraph/blob/0c13ab2fbd95306272528ed781511d7e2aa338d3/networks.json).
