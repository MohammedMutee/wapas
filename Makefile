.PHONY: help venv install up down migrate seed eval eval-llm warm calibrate sweep triage redteam demo dashboard secrets replay test lint typecheck fmt clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

# Use whichever Docker endpoint actually answers. A machine with Docker Desktop
# installed but not running leaves the active context pointing at a dead socket,
# so fall back to the system daemon rather than failing.
DC := docker compose
ifeq ($(shell docker info >/dev/null 2>&1 && echo ok),)
  DC := DOCKER_HOST=unix:///var/run/docker.sock docker compose
endif

help:            ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

venv:            ## Create the virtualenv
	python3 -m venv $(VENV)

install: venv    ## Install the package and dev dependencies
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

up:              ## Start Postgres + Redis
	$(DC) up -d
	@echo "waiting for postgres..." && until $(DC) exec -T db pg_isready -U wapas >/dev/null 2>&1; do sleep 1; done
	@echo "ready."

down:            ## Stop the stack
	$(DC) down

migrate:         ## Apply database migrations
	$(VENV)/bin/alembic upgrade head

seed:            ## Load a seeded synthetic scenario
	$(PY) -m wapas.cli seed --scenario sim/scenarios/baseline.yaml

eval:            ## Run the full batch evaluation and regenerate results/report.md
	$(PY) -m eval.run_batch --seed $${SEED:-20260901}

warm:            ## Pre-fetch every distinct diagnosis prompt into the local cache
	$(PY) scripts/warm_diagnoses.py --seed $${SEED:-20260901} --workers $${WORKERS:-8}

eval-llm: warm   ## Run the evaluation with the LLM agent as the treatment arm
	$(PY) -m eval.run_batch --seed $${SEED:-20260901} --llm

calibrate:       ## Measure the false-positive rate of the evaluation on known nulls
	$(PY) -m eval.calibrate --seeds $${SEEDS:-300}

sweep:           ## Sensitivity: every parameter +/-30%, do the conclusions hold?
	$(PY) -m eval.sensitivity --factor $${FACTOR:-0.30}

triage:          ## Is it ever right to refuse to chase a payment? (ships off; see results/triage.md)
	$(PY) -m eval.triage_study

redteam:         ## Run the adversarial suite. Expect 0 escapes.
	$(PY) -m redteam.run

dashboard:       ## Render results/summary.json into one self-contained HTML page
	$(PY) dashboard/build.py

secrets:         ## Scan the working tree and full git history for credentials
	@$(PY) -m detect_secrets scan --all-files >/dev/null 2>&1 || $(PIP) install -q detect-secrets
	@git log --all -p 2>/dev/null | grep -icE 'rzp_(test|live)_[A-Za-z0-9]{10}|nvapi-[A-Za-z0-9_-]{20}' \
	  | xargs -I{} sh -c 'test {} -le 1 || (echo "SECRET IN GIT HISTORY"; exit 1)'
	@echo "no credentials in git history; .env is ignored and untracked"

demo:            ## One real episode end-to-end against Razorpay test mode
	$(PY) scripts/live_demo.py $${ARGS:-}

serve:           ## Run the live service: webhook endpoint, episodes held open
	$(VENV)/bin/uvicorn wapas.api:create_app --factory --host 127.0.0.1 --port $${PORT:-8000}

replay:          ## Re-derive an episode from the audit chain: make replay EP=<uuid>
	$(PY) -m wapas.cli replay $(EP)

test:            ## Run unit + property tests
	$(VENV)/bin/pytest

lint:            ## Lint
	$(VENV)/bin/ruff check src tests eval sim scripts redteam

fmt:             ## Format
	$(VENV)/bin/ruff format src tests eval sim scripts redteam
	$(VENV)/bin/ruff check --fix src tests eval sim scripts redteam

typecheck:       ## Strict typing on the modules where bugs are credibility bugs
	$(VENV)/bin/mypy

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
