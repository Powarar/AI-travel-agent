#!/usr/bin/env bash
# Бутстрап dev-окружения: .env -> образы -> стек -> ожидание healthy postgres.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "создан .env из .env.example (значения по умолчанию для локальной разработки)"
fi

docker compose --profile tools build
docker compose --profile tools up -d

echo "ждём postgres..."
for _ in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -q; then
    break
  fi
  sleep 2
done

docker compose ps -a

cat <<EOF

Дальше:
  web      http://localhost:$(grep -m1 WEB_PORT .env | cut -d= -f2)
  backend  http://localhost:$(grep -m1 BACKEND_PORT .env | cut -d= -f2)/docs
  agent    http://localhost:$(grep -m1 AGENT_PORT .env | cut -d= -f2)
  psql     make psql
  seed     make seed

EOF
