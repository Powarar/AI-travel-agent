# Release verification — 2026-11-19

Verified on the separate `travel-production` Compose project using public local-only
acceptance credentials and deterministic model provider.

- Release backend and multi-stage frontend images built successfully.
- Alembic migrations applied to an empty PostgreSQL database.
- Backend starts as UID 10001 with read-only root filesystem and dropped capabilities.
- Caddy configuration validated and public HTTP routing exercised.
- Authenticated HTTP acceptance: all eight checks passed, including `plan_ready`,
  no blocking violations, private monitoring (both URL prefixes) and private docs.
- PostgreSQL custom dump restored into a separate `restore_check` database;
  restored catalogue contains 1,318 hotels.
- Python regression: 493 passed, four explicit live skips; Ruff and mypy clean.
- Frontend dependency installation and production build passed.
- Offline evaluation in the preceding local verification: 16/16 scenarios.

Local release URLs: public `http://localhost`, operator monitoring
`http://localhost:18080/monitoring.html`, operator API `http://localhost:18010`.

TLS certificate issuance requires the actual server DNS and inbound 80/443; local
acceptance uses `http://localhost`. No public certificate issuance, live supplier
booking, load SLO or new live-model accuracy is claimed by this release check.

Deployment instructions: [deploy/README.md](../deploy/README.md).
