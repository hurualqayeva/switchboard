# Changelog

## Unreleased

### Phase 1 - gateway core (2026-10-31)

- OpenAI-compatible `POST /v1/chat/completions` (streaming and non-streaming),
  `POST /v1/embeddings`, `GET /v1/models`, with every error in the OpenAI envelope.
- Provider adapters: `openai_compat` (OpenAI, vLLM, llama.cpp), `anthropic` (Messages API
  with tool and image translation), `bedrock` (Converse and ConverseStream).
- Model catalog (`catalog/models.yaml`) with prices, capabilities and timeouts.
- Tenants, roles and API keys (SHA-256, lookup prefix); admin API and `switchboard-admin` CLI.
- Usage ledger: partitioned `usage_events`, batched async writer, per-request cost in USD,
  admin usage summary with p95 latency.
- Postgres and in-memory storage behind one interface; forward-only SQL migrator.

### Phase 0 - skeleton and tooling (2026-10-30)

- Project layout, `pyproject.toml` with ruff, mypy strict, pytest, coverage floor 85.
- FastAPI app factory with `/healthz`, `/readyz`, request id middleware, structured logging.
- Docker Compose for Postgres 16, Redis Stack, llama.cpp (CPU stand-in for vLLM),
  OpenTelemetry collector, Tempo, Prometheus, Grafana with provisioned datasources.
- GitHub Actions CI, pre-commit hooks, ADR template and six initial ADRs.
