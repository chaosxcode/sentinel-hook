# Data-pipeline evidence

This directory contains small, committed extraction receipts for the first
Sentinel research milestone.

- `unichain-mainnet-smoke-2026-08-23/` captures a fixed 1,001-block mainnet
  window. It demonstrates current production-chain ingestion without claiming
  that the window is representative or that its pools pass Gate 1.
- `unichain-sepolia-demo/` captures the blocks containing Sentinel's own pool
  initialization, liquidity, and live demo swaps. It connects the existing
  deployment evidence to the same decoder intended for historical research.

Each manifest includes the exact block hashes and `events.jsonl` SHA-256. Run
the offline verifier documented in `research/README.md` before using a receipt.
