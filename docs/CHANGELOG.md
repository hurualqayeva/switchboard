# Changelog

## Unreleased

### Phase 0 - skeleton and tooling (2026-10-30)

- Project layout, `pyproject.toml` with ruff, mypy strict, pytest, coverage floor 85.
- FastAPI app factory with `/healthz`, `/readyz`, request id middleware, structured logging.
- Docker Compose for Postgres 16, Redis Stack, llama.cpp (CPU stand-in for vLLM),
  OpenTelemetry collector, Tempo, Prometheus, Grafana with provisioned datasources.
- GitHub Actions CI, pre-commit hooks, ADR template and six initial ADRs.
