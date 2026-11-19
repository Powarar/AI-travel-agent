#!/usr/bin/env bash
# Бутстрап dev-окружения: .env -> образы -> стек -> ожидание healthy postgres.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  # Ключ подписи JWT на эту машину: иначе сессии не переживают рестарт backend.
  python3 -c 'import pathlib, secrets; p = pathlib.Path(".env"); p.write_text(p.read_text().replace("AUTH_JWT_SECRET=", "AUTH_JWT_SECRET=" + secrets.token_urlsafe(48), 1))'
  echo "создан .env из .env.example (значения по умолчанию для локальной разработки)"
fi

docker compose build
docker compose up -d

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
  web      http://localhost:$(grep -m1 NGINX_PORT .env | cut -d= -f2)   # nginx: единая точка входа (API + UI)
  backend  http://localhost:$(grep -m1 BACKEND_PORT .env | cut -d= -f2)/docs   # напрямую, только 127.0.0.1
  psql     make psql
  seed     make seed

EOF
