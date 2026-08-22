.PHONY: help venv install up down migrate seed eval redteam replay test lint typecheck fmt clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

help:            ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

venv:            ## Create the virtualenv
	python3 -m venv $(VENV)

install: venv    ## Install the package and dev dependencies
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

up:              ## Start Postgres + Redis
	docker compose up -d
	@echo "waiting for postgres..." && until docker compose exec -T db pg_isready -U wapas >/dev/null 2>&1; do sleep 1; done
	@echo "ready."

down:            ## Stop the stack
	docker compose down

migrate:         ## Apply database migrations
	$(VENV)/bin/alembic upgrade head

seed:            ## Load a seeded synthetic scenario
	$(PY) -m wapas.cli seed --scenario sim/scenarios/baseline.yaml

eval:            ## Run the full batch evaluation and regenerate results/report.md
	$(PY) -m eval.run_batch --seed $${SEED:-20260901}

redteam:         ## Run the adversarial suite. Expect 0 escapes.
	$(PY) -m redteam.run

replay:          ## Re-derive an episode from the audit chain: make replay EP=<uuid>
	$(PY) -m wapas.cli replay $(EP)

test:            ## Run unit + property tests
	$(VENV)/bin/pytest

lint:            ## Lint
	$(VENV)/bin/ruff check src tests eval sim redteam

fmt:             ## Format
	$(VENV)/bin/ruff format src tests eval sim redteam
	$(VENV)/bin/ruff check --fix src tests eval sim redteam

typecheck:       ## Strict typing on the modules where bugs are credibility bugs
	$(VENV)/bin/mypy

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
