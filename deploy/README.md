# Server deployment

Requirements: Linux host, Docker Engine with Compose v2, DNS A/AAAA record pointing
to the host, inbound TCP 80/443 and UDP 443. Allow SSH only from operator addresses.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Generate two independent secrets for `POSTGRES_PASSWORD` and `AUTH_JWT_SECRET`.
Set `DOMAIN` to the real hostname. Use URL-safe database passwords. Never deploy
`deploy/acceptance.env`: those credentials are public and only for local tests.
Set model routes and provider credentials explicitly to enable live models; the
default deterministic provider supports offline operation.

The nginx API limiter currently sees the Caddy peer and therefore provides a global
2 requests/second safety cap with burst 10. Increase capacity only with a trusted-proxy
client-IP configuration and load testing. It is intentionally conservative for the
single-process release; auth additionally has application login throttling.

## Release

```bash
docker compose --env-file .env.production -f compose.production.yml build
docker compose --env-file .env.production -f compose.production.yml up -d postgres
docker compose --env-file .env.production -f compose.production.yml run --rm backend alembic upgrade head
docker compose --env-file .env.production -f compose.production.yml up -d
curl --fail https://YOUR_DOMAIN/health/ready
```

Caddy provisions and renews TLS certificates. Certificate and database volumes
must persist across deployments. Backend exposes no public host port; loopback
18010 is for operators, and loopback 18080 serves the monitoring HTML.

The database initially has no catalogue. Populate it through a reviewed ingestion
job. To load the synthetic development catalogue intentionally, use the existing
seed script with the production DB connection only after reviewing its destructive
TRUNCATE behavior. Do not run it against supplier inventory.

For a new empty catalogue, generate an SQL file with
`python scripts/seed_database.py --emit-sql /tmp/travel-catalogue.sql`, inspect it, then
pipe it to `docker compose --env-file .env.production -f compose.production.yml exec -T postgres psql -U travel -d travel -v ON_ERROR_STOP=1`.
This is an explicit synthetic-catalogue initialization, not supplier ingestion.

## Monitoring

```bash
ssh -L 18010:127.0.0.1:18010 -L 18080:127.0.0.1:18080 USER@SERVER
```

Open `http://localhost:18080/monitoring.html`. The dashboard uses the same-origin
API on the operator web listener; public traffic is separately restricted by Caddy.
Use `http://localhost:18010/v1/monitoring/models` and `/v1/monitoring/accuracy` for raw
JSON. Latency is measured on completed calls. Cost with incomplete coverage is a
subtotal, not a bill. Evaluate accuracy with labelled datasets; do not infer it from
HTTP status. Monitor `/health/ready` externally and retain Docker logs.

## Backup and restore rehearsal

```bash
docker compose --env-file .env.production -f compose.production.yml exec -T postgres pg_dump -U travel -d travel -Fc > travel.dump
docker compose --env-file .env.production -f compose.production.yml exec -T postgres createdb -U travel restore_check
docker compose --env-file .env.production -f compose.production.yml exec -T postgres pg_restore -U travel -d restore_check --exit-on-error < travel.dump
```

Verify row counts and constraints in `restore_check`. Store encrypted backups on a
different host with a retention policy. An on-host volume alone is not a backup.
Never restore over the active database without a maintenance and recovery plan.

## Release gates

Run `make test`, `make lint`, `make eval`, and frontend `npm ci && npm run build`.
Record the Git revision, image digest and provider configuration with each release.
Keep the previous application image for rollback. Review migration compatibility
before rolling back; automatic downgrade can destroy data.

Run `.venv/bin/python -m evaluation.run_release_smoke --url https://YOUR_DOMAIN`
after deployment. It creates a dedicated account and trip and checks privacy routes,
authentication and a validated plan. With a live provider configured it is billable.

This deployment supports one backend process. Authentication throttles and run
summary counters are process-local. Horizontal scaling requires shared throttling
and metrics. Capacity, supplier booking and retrieval relevance require their own
acceptance; the release does not imply measured production load SLOs.
