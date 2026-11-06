DC := docker compose
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PSQL := $(DC) exec postgres psql -U ostrovok -d ostrovok

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "make up        — start the local stack and apply migrations"
	@echo "make setup     — create the Python development environment"
	@echo "make web       — start the frontend with hot reload on http://localhost:5173"
	@echo "make down      — stop the stack (the pgdata volume is retained)"
	@echo "make build     — build service images"
	@echo "make rebuild   — rebuild service images without cache"
	@echo "make ps        — show container status"
	@echo "make logs      — follow logs from all services"
	@echo "make seed      — load the development catalogue"
	@echo "make migrate   — run Alembic migrations in the backend"
	@echo "make eval      — run offline evaluation scenarios"
	@echo "make eval-live — run explicitly enabled live-model acceptance checks"
	@echo "make warmup    — prepare the ingestion adapter cache"
	@echo "make psql      — open a PostgreSQL shell"
	@echo "make test      — run the Python test suite"
	@echo "make lint      — run type checks and validation guardrails"
	@echo "make bot       — start the bot (requires TELEGRAM_BOT_TOKEN in .env)"
	@echo "make clean     — remove containers, local images, and the pgdata volume"

.PHONY: setup
setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -r requirements-dev.txt

.PHONY: up
up:
	$(DC) up -d --build postgres backend agent web
	$(MAKE) migrate

.PHONY: web
web:
	$(DC) up -d --build web

.PHONY: down
down:
	$(DC) down

.PHONY: build
build:
	$(DC) build

.PHONY: rebuild
rebuild:
	$(DC) build --no-cache

.PHONY: ps
ps:
	$(DC) ps -a

.PHONY: logs
logs:
	$(DC) logs -f --tail=100

.PHONY: seed
seed:
	$(PYTHON) scripts/seed_database.py

.PHONY: migrate
migrate:
	$(DC) exec backend python -c "from alembic import command; from alembic.config import Config; c=Config('/app/alembic.ini'); c.set_main_option('script_location', '/app/app/db/migrations'); command.upgrade(c, 'head')"

.PHONY: psql
psql:
	$(PSQL)

.PHONY: test
test:
	$(PYTHON) -m pytest tests

.PHONY: eval
eval:
	$(PYTHON) -m evaluation.run_eval --json

.PHONY: eval-live
eval-live:
	@test "$(RUN_LIVE_EVALS)" = "1" || (echo "Set RUN_LIVE_EVALS=1 to allow billable live-model checks" >&2; exit 2)
	$(PYTHON) -m pytest tests/scenarios/test_parse_accuracy.py tests/scenarios/test_live_agent_e2e.py -q

.PHONY: warmup
warmup:
	$(PYTHON) -m services.backend.app.ingestion.warmup

.PHONY: index-search
index-search:
	$(DC) exec backend python -m app.ingestion.search_index

.PHONY: eval-async-live
eval-async-live:
	$(PYTHON) -m evaluation.run_async_e2e

.PHONY: eval-accuracy
eval-accuracy:
	$(DC) exec -e RUN_LIVE_EVALS=$(RUN_LIVE_EVALS) backend python -m evaluation.run_parse_accuracy --live --persist

.PHONY: lint
lint:
	$(PYTHON) -m mypy services/backend/app services/agent/app shared/contracts
	@if grep -rnE '^\s*(import|from) (openai|instructor|litellm|anthropic)' services/agent/app/validation; then exit 1; fi

.PHONY: bot
bot:
	docker compose --profile bot --profile tools up -d bot

.PHONY: clean
clean:
	$(DC) --profile bot down -v --rmi local
