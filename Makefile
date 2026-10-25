# Все сервисные команды идут через docker compose; локальный python/node не нужен.
DC := docker compose --profile tools
PSQL := $(DC) exec postgres psql -U ostrovok -d ostrovok

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "make up        — postgres+backend+agent+web (hot reload)"
	@echo "make down      — остановить стек (данные в volume pgdata остаются)"
	@echo "make build     — пересобрать образы"
	@echo "make rebuild   — пересобрать без кэша"
	@echo "make ps        — статус контейнеров"
	@echo "make logs      — логи всех сервисов"
	@echo "make seed      — одноразовый запуск scripts/seed_database.py"
	@echo "make migrate   — alembic upgrade head внутри backend"
	@echo "make psql      — консоль postgres"
	@echo "make test      — pytest в backend и agent"
	@echo "make lint      — ruff + mypy в backend и agent"
	@echo "make bot       — поднять бота (нужен TELEGRAM_BOT_TOKEN в .env)"
	@echo "make clean     — остановить и удалить контейнеры, образы и volume pgdata"

.PHONY: up
up:
	$(DC) up -d

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
	$(DC) run --rm seed

.PHONY: migrate
migrate:
	$(DC) exec backend alembic upgrade head

.PHONY: psql
psql:
	$(PSQL)

.PHONY: test
test:
	$(DC) exec backend pytest
	$(DC) exec agent pytest

.PHONY: lint
lint:
	$(DC) exec backend ruff check .
	$(DC) exec backend mypy app
	$(DC) exec agent ruff check .
	$(DC) exec agent mypy app

.PHONY: bot
bot:
	docker compose --profile bot --profile tools up -d bot

.PHONY: clean
clean:
	$(DC) --profile bot down -v --rmi local
