# Switchboard

Multi-tenant LLM inference and routing platform. One OpenAI-compatible API in front of
Amazon Bedrock, OpenAI, Anthropic and self-hosted vLLM on EKS GPU spot nodes. Routes every
request to the cheapest model that passes the eval threshold for its task class, caches
semantically, enforces budgets and rate limits per tenant, and records a cost ledger for
every call.

Status: design complete, build starting. See `docs/ARCHITECTURE.md` and `docs/PLAN.md`.

## Headline numbers

Filled in at the end of the benchmark phase.

| Metric | Value |
|--------|-------|
| Gateway overhead p99 | TBD |
| vLLM throughput on one A10G (tokens/s) | TBD |
| Cost reduction vs all-premium routing at equal quality | TBD |
| Cold start to first token from zero GPU nodes | TBD |

## Run locally

Requires Docker, [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it).

```bash
uv sync                                        # install the project and dev tools
docker compose -f deploy/compose/docker-compose.yml up -d --wait postgres redis
uv run switchboard                             # gateway on http://localhost:8000
uv run pytest --cov                            # tests
```

Optional stacks, enabled with compose profiles:

```bash
# local CPU model (llama.cpp, Qwen2.5-1.5B) standing in for the vLLM GPU tier
docker compose -f deploy/compose/docker-compose.yml --profile llm up -d --wait
# observability: OpenTelemetry collector, Tempo, Prometheus, Grafana on http://localhost:3000
docker compose -f deploy/compose/docker-compose.yml --profile obs up -d --wait
```

With GNU make installed, `make up`, `make up-all`, `make check` and `make run` wrap the same commands.

## Layout

See section 9 of `docs/ARCHITECTURE.md`.

## Decisions

Architecture decision records live in `docs/adr/`.
