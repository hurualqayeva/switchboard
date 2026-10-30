COMPOSE := docker compose -f deploy/compose/docker-compose.yml

.PHONY: install up up-all down logs test lint type check fmt run eval load clean

install:        ## Install the project and dev tools
	uv sync

up:             ## Start Postgres and Redis
	$(COMPOSE) up -d --wait postgres redis

up-all:         ## Start everything: data stores, local LLM, observability stack
	$(COMPOSE) --profile llm --profile obs up -d --wait

down:           ## Stop and remove containers (volumes are kept)
	$(COMPOSE) --profile llm --profile obs down

logs:           ## Tail container logs
	$(COMPOSE) --profile llm --profile obs logs -f --tail=100

test:           ## Run the test suite with coverage
	uv run pytest --cov

lint:           ## Ruff lint and format check
	uv run ruff check . && uv run ruff format --check .

type:           ## mypy strict
	uv run mypy

check: lint type test   ## Everything CI runs

fmt:            ## Auto-fix lint and format
	uv run ruff check --fix . && uv run ruff format .

run:            ## Run the gateway with reload
	uv run switchboard

eval:           ## Run the eval harness (phase 4)
	uv run python -m evals.run

load:           ## Run k6 load tests (phase 3)
	k6 run load/gateway_overhead.js

clean:          ## Remove containers and volumes
	$(COMPOSE) --profile llm --profile obs down -v
