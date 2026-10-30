# ADR-0001: Hand-written provider adapters instead of wrapping LiteLLM

Date: 2026-10-30
Status: accepted

## Context

LiteLLM already normalises 100+ providers to the OpenAI schema and I have used it in
production. Wrapping it would save the adapter work in phase 1.

## Decision

Write three adapters by hand: `openai_compat` (OpenAI, vLLM, llama.cpp), `anthropic`,
and `bedrock` (Converse API). Keep the adapter protocol small (complete, stream, embed,
count_tokens) so a LiteLLM-backed adapter could be added later if a fourth provider is
ever needed.

## Consequences

- The routing, fallback, cost and streaming logic is demonstrably mine, which is the
  point of the project. "Wrapped LiteLLM" is not a senior-level story.
- Full control over streaming semantics, timeouts and error classification, which the
  circuit breaker depends on.
- Fewer dependencies in the hot path; measurable gateway overhead.
- Cost: three adapters to maintain, each with recorded-fixture contract tests.
