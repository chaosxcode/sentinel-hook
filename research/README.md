# Sentinel research pipeline

This directory begins the repo's Weeks 1–2 milestone: reproducible raw-data
ingestion before any Gate 1 modeling or performance claim.

**Current measured findings and next checkpoint:**
[`FINDINGS.md`](FINDINGS.md) (last updated 2026-08-23).

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

## What this does not prove

These receipts prove that Sentinel can reproducibly ingest and decode raw v4
events. They do **not** pass Gate 1. The next research increment must freeze
pool selection, token metadata, reference-price alignment, adverse-selection
labels, leakage controls, exclusions, and the validation split before running
the three-pool Gate 1 study.

The event conventions follow the Uniswap Foundation's
[v4 data guide](https://www.uniswapfoundation.org/blog/how-to-navigate-uniswap-v4-data).
The configured PoolManager addresses and deployment blocks come from the
[official v4 subgraph network registry](https://github.com/Uniswap/v4-subgraph/blob/0c13ab2fbd95306272528ed781511d7e2aa338d3/networks.json).
