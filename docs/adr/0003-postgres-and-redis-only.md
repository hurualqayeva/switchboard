# ADR-0003: Postgres and Redis only, no message broker

Date: 2026-10-30
Status: accepted

## Context

The ledger, budgets and rate limits need shared state. A broker (Kafka, SQS) would
decouple ledger writes from the request path and look impressive on a diagram.

## Decision

Postgres for durable state (tenants, keys, budgets, usage events, routing tables,
eval runs). Redis for hot state (rate-limit buckets, breaker state, budget counters,
semantic cache). Ledger writes are batched in-process and flushed asynchronously; budget
counters are incremented in Redis and written behind to Postgres every few seconds.

## Consequences

- Two stores to operate, both managed on AWS (RDS, ElastiCache) and trivial locally.
- Ledger rows can be lost if a gateway pod dies between batch flushes (bounded by the
  flush interval, default 1 s). Acceptable for usage accounting; documented.
- A broker can be inserted behind the ledger writer interface later without touching the
  request path. Not building it now keeps the diagram honest.
