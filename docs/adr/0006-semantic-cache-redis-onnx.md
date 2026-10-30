# ADR-0006: Semantic cache in Redis with a CPU ONNX embedding model

Date: 2026-10-30
Status: accepted

## Context

A semantic cache needs an embedding per request and a vector store. Options: call a
provider embedding API (adds latency and cost per request), run sentence-transformers
with torch in the gateway (heavy image, slow cold start), or run a small model with ONNX
Runtime. Store options: Redis Stack vector index, pgvector, a separate vector DB.

## Decision

`bge-small-en-v1.5` exported to ONNX, executed with ONNX Runtime on CPU inside the
gateway process. Redis Stack vector index with a per-tenant, per-model-class namespace,
cosine threshold 0.95, TTL 24 hours. Exact-hash tier checked first.

## Consequences

- Embedding cost is roughly 10 ms on CPU and zero dollars; the gateway image stays small
  (no torch).
- Redis is already present for rate limits and breakers; no new store.
- Threshold 0.95 is conservative; the eval harness includes a cache-safety check that
  replays near-duplicate prompts and flags any hit that changes the expected answer.
- Only low-temperature, tool-free requests are eligible; everything else bypasses.
