# Switchboard - Build Plan

Companion to `ARCHITECTURE.md`. Seven phases, each with a demoable milestone and a
definition of done. Effort is in focused working days; the whole plan is roughly 5 to 6
weeks at 4 to 5 days a week. Phases 0 to 4 run entirely on the laptop. AWS is touched
only in phases 5 and 6, in short sessions, and destroyed after each one.

Rules for the whole build:

- Every phase ends with a tag, a short entry in `docs/CHANGELOG.md`, and the README updated.
- Every design choice that was not obvious gets an ADR, even a five-line one.
- Numbers go in `docs/benchmarks/` with the exact command that produced them.
- No feature is "done" until it has a test and shows up in a Grafana panel or the ledger.

## Phase 0 - Skeleton and tooling (1 day)

Goal: a repo a senior reviewer would trust at first glance.

- [x] `uv init`, pin Python 3.12 (`uv python install 3.12`), `pyproject.toml` with ruff, mypy (strict), pytest, pytest-asyncio, hypothesis, coverage floor 85.
- [x] Pre-commit: ruff, mypy, detect-secrets, conventional commit message check.
- [ ] GitHub repo (public), branch protection on `main`. `ci.yml` is written and runs lint, type check, tests.
- [x] `docs/adr/` with template and the six ADRs already drafted.
- [x] `deploy/compose/docker-compose.yml`: Postgres 16, Redis Stack, llama.cpp server with a 1.5B GGUF, Prometheus, Grafana, Tempo, OTel collector.
- [x] `Makefile`: `up`, `up-all`, `down`, `test`, `lint`, `type`, `check`, `fmt`, `run`, `eval`, `load`, `clean`.
- [x] App skeleton: FastAPI factory, `/healthz`, `/readyz`, request id middleware, structured logging, settings. 11 tests, 95 percent coverage.

Done when: `make up && make test` is green on a fresh clone and CI is green.

## Phase 1 - Gateway core (5 days)

Goal: an OpenAI-compatible gateway that streams from three providers and records every request.

- [ ] Pydantic models for chat completions, embeddings, SSE chunks (match the OpenAI schema exactly; test with the official `openai` Python client as a consumer).
- [ ] `ProviderAdapter` protocol and `openai_compat` adapter (works against llama.cpp locally).
- [ ] `anthropic` adapter: system prompt, tool calls, streaming event translation.
- [ ] `bedrock` adapter: Converse and ConverseStream via aioboto3; recorded fixtures for tests so CI needs no AWS credentials.
- [ ] Model catalog YAML loader with price and capability data; cost calculator.
- [ ] Auth: tenants, argon2-hashed API keys, roles; `POST /admin/tenants`, `POST /admin/keys`.
- [ ] Usage ledger: `usage_events` table (monthly partitions), batched async writer, request context propagation.
- [ ] Contract tests per adapter from recorded fixtures; integration tests with testcontainers.

Done when: the `openai` client pointed at `localhost:8000` streams a completion from llama.cpp, Anthropic and Bedrock by model id, and each call appears in `usage_events` with correct cost.

## Phase 2 - Router, limits, resilience (5 days)

Goal: `auto` routing with fallback, budgets and canaries, fully explainable.

- [ ] Rate limiting: Redis token bucket per key (RPM and TPM), `429` with `Retry-After`.
- [ ] Budgets: monthly soft and hard limits, Redis counter with write-behind, `402` on hard limit, warning header on soft.
- [ ] Router policy engine: aliases, tenant overrides, routing table loader (hot reload), hard-constraint filter, cost and latency sort, reason string.
- [ ] Task classifier v1: logistic regression over bge-small embeddings, trained from the eval dataset labels (phase 4 improves the data; use a 100-item seed set now).
- [ ] Circuit breaker per deployment in Redis (sliding window, half-open), fallback chain in executor, retry budget.
- [ ] Experiments: weighted split, sticky by key hash, `x-switchboard-experiment` header, ledger column.
- [ ] Property-based tests on the router (hypothesis): never picks a deployment that violates a hard constraint, always returns a non-empty chain when one exists.
- [ ] Mutation testing on `router/` and `limits/` with mutmut; target kill rate above 90 percent.

Done when: killing the llama.cpp container mid-load makes the gateway fall back to the next deployment with zero client-visible errors, and the ledger shows the breaker opening.

## Phase 3 - Cache and observability (4 days)

Goal: every request is traceable and cache savings are measured in dollars.

- [ ] Exact-hash cache tier with request normalisation.
- [ ] Semantic cache tier: bge-small via ONNX Runtime, Redis vector index, per-tenant namespace, eligibility rules, TTL.
- [ ] OpenTelemetry traces with the spans listed in the architecture; export to Tempo locally.
- [ ] Prometheus metrics listed in the architecture; `/metrics` endpoint; ServiceMonitor manifest.
- [ ] Grafana dashboards as JSON: Traffic and latency, Cost and budgets, Cache and routing.
- [ ] Alert rules (Prometheus): error rate, p99, budget 80 percent, breaker open.
- [ ] Structured JSON logging with request id; body logging off by default.
- [ ] `docs/benchmarks/gateway-overhead.md`: k6 against a stub provider, report p50/p95/p99 overhead. Target: p99 under 15 ms.

Done when: one trace in Tempo shows auth, cache miss, route decision, provider call and ledger write for a single request id, and the cost dashboard shows cache savings in USD.

## Phase 4 - Eval harness and fine-tuning (6 days)

Goal: the routing table is generated from evidence and gated in CI; one LoRA adapter beats a premium model on cost at equal quality.

- [ ] Dataset builder: pull slices of Spider, XSum, GSM8K, HumanEval; hand-build a 60-item financial extraction set with a fixed JSON schema; label by task class; version to S3 (local folder until phase 5).
- [ ] Scorers: exact/F1, SQL execution accuracy (SQLite), pass@1 in a subprocess sandbox, rubric judge with a strong model; judge calibration on 50 human-labelled items, agreement reported.
- [ ] Runner: evaluates every catalog deployment per task class through the gateway itself (so latency and cost are real), writes `eval_runs`, emits quality, cost and latency matrices and a Markdown report.
- [ ] Routing table generator: cheapest deployment per task class above threshold; diff against the active table; `evals.yml` gate.
- [ ] Retrain the task classifier on the full dataset; report accuracy and confusion matrix.
- [ ] Fine-tune: LoRA on Qwen2.5-7B-Instruct with Unsloth for the financial extraction task (free Colab or Kaggle GPU is enough for this size); push the adapter to the adapters folder; add to the catalog as `qwen2.5-7b:fin-extract`.
- [ ] Re-run the harness: show extraction quality parity with the premium model and record cost per request for both.

Done when: `make eval` produces a report, a routing table PR, and CI fails when a model's score is artificially lowered in a fixture.

## Phase 5 - AWS and GPU serving (6 days, 2 to 3 AWS sessions)

Goal: everything runs on EKS from Terraform, with vLLM on spot GPUs scaling from zero.

- [ ] Install Terraform, AWS CLI, Helm, kind locally. Create an AWS account budget alert at 50 and 100 USD.
- [ ] Terraform modules in the order: network, registry, storage, iam, data, eks, karpenter, platform, secrets. Remote state in S3 with DynamoDB lock. Checkov in `infra.yml`.
- [ ] Helm chart: gateway Deployment with HPA, ConfigMaps for catalog and routing table, ExternalSecrets, NetworkPolicies, ServiceMonitors, Ingress via ALB. Test on kind first with llama.cpp standing in for vLLM.
- [ ] vLLM image: AWQ base weights baked in (measure image size and pull time), adapter sync init container from S3, `--enable-lora` flags from the architecture.
- [ ] Karpenter GPU NodePool (g5 spot, on-demand fallback, taint, consolidation) and KEDA ScaledObject on `vllm:num_requests_waiting`, min 0 max 2.
- [ ] Fluent Bit to CloudWatch; kube-prometheus-stack with the dashboards from phase 3 imported.
- [ ] `image.yml` and `deploy.yml` workflows; OIDC federation from GitHub Actions to AWS, no long-lived keys.
- [ ] `docs/benchmarks/cold-start.md`: measured time from first request to first token at scale-from-zero. Decide image-baked vs EBS snapshot and write the ADR.

Done when: `terraform apply` from a clean state yields a working gateway URL; an `extraction` request routes to the LoRA adapter on a spot GPU that did not exist a minute earlier; `terraform destroy` leaves nothing behind.

## Phase 6 - Benchmarks, demo agent, write-up (4 days)

Goal: the numbers and the story.

- [ ] k6 scenarios: gateway overhead, vLLM throughput at concurrency 1/8/32/64 (tokens/s, TTFT, p99), cache hit ratio under a realistic replay, breaker behaviour under injected 500s.
- [ ] `docs/benchmarks/serving.md` with tables and the exact commands.
- [ ] Demo analyst agent (LangGraph) with side-by-side ledger: routed vs all-premium; record the cost reduction percentage at equal eval quality.
- [ ] README: one-screen pitch, architecture diagram, the three headline numbers, a 90-second GIF of the demo, how to run locally in three commands.
- [ ] Short engineering write-up (blog style) in `docs/writeup.md`: decisions, numbers, what went wrong.
- [ ] Replace the object detection project on the CV with the Switchboard entry; rewrite the NascTech LiteLLM bullet to reference it.
- [ ] Optional: admin React dashboard if time remains.

Done when: a stranger can read the README, run it locally in under ten minutes, and quote three numbers from it.

## CV bullets this plan produces

Draft now, fill numbers at the end:

- Designed and built Switchboard, a multi-tenant LLM gateway routing across Amazon Bedrock, OpenAI, Anthropic and self-hosted vLLM (AWQ, multi-LoRA) on EKS GPU spot nodes with KEDA scale-to-zero; Terraform, Helm, GitHub Actions with OIDC.
- Built an eval harness (N prompts, 7 task classes, execution and judge scorers) that generates the routing table and gates CI on regression; cut cost per request by X percent versus all-premium routing at equal quality.
- Fine-tuned a LoRA adapter for financial extraction that matched the premium model's score at Y percent of its cost, served through vLLM multi-LoRA.
- Measured p99 gateway overhead of N ms and M tokens/s on a single A10G; semantic cache saved Z percent of spend on replayed traffic.

## Order of the first week

1. Phase 0 in full.
2. Phase 1 adapters in this order: `openai_compat` against llama.cpp (unblocks everything), then `anthropic`, then `bedrock` with recorded fixtures.
3. Ledger before router: cost data must exist before routing logic is written against it.
