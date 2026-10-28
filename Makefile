DC := docker compose
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PSQL := $(DC) exec postgres psql -U ostrovok -d ostrovok

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "make up        — postgres+backend+agent и базовая миграция"
	@echo "make web       — только фронтенд (hot reload), http://localhost:5173"
	@echo "make down      — остановить стек (данные в volume pgdata остаются)"
	@echo "make build     — пересобрать образы"
	@echo "make rebuild   — пересобрать без кэша"
	@echo "make ps        — статус контейнеров"
	@echo "make logs      — логи всех сервисов"
	@echo "make seed      — одноразовый запуск scripts/seed_database.py"
	@echo "make migrate   — alembic upgrade head внутри backend"
	@echo "make eval      — офлайн evaluation 12 сценариев"
	@echo "make warmup    — подготовить кэш ingestion-адаптера"
	@echo "make psql      — консоль postgres"
	@echo "make test      — весь набор pytest"
	@echo "make lint      — mypy и запрет LLM-импортов в валидаторе"
	@echo "make bot       — поднять бота (нужен TELEGRAM_BOT_TOKEN в .env)"
	@echo "make clean     — остановить и удалить контейнеры, образы и volume pgdata"

.PHONY: up
up:
	$(DC) up -d --build postgres backend agent
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

.PHONY: warmup
warmup:
	$(PYTHON) -m services.backend.app.ingestion.warmup

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
