# Operations

## Local acceptance

```bash
make setup
make up
make seed
make test
make lint
make eval
```

`make seed` replaces development catalogue data. Never run it against supplier inventory.
Open http://localhost:8080 and http://localhost:8080/monitoring.html.
The dashboard polls every 15 seconds and displays errors explicitly. Model latency and
cost come from persisted HTTP events; run outcomes are process-local. Evaluation accuracy
requires a labelled dataset. An `infeasible` outcome can be the correct result.

## Paid acceptance

Configure provider credentials and model routes in `.env`, then recreate backend so it
receives the environment. Provider calls spend money. Run only bounded acceptance suites:

```bash
RUN_LIVE_EVALS=1 make eval-accuracy
```

`RUN_LIVE_EVALS=1 make eval-async-live` creates a dedicated acceptance account and uses
its Bearer token for the complete HTTP journey. Account and trips remain in the database
for investigation. Historical artifacts describe their recorded revision and do not
establish acceptance of a newer deployment.

## Server preparation

The checked-in Compose configuration is a development stack: bind mounts, Vite HMR,
HTTP and local database defaults. Before internet exposure, prepare a separate release
configuration with a built frontend, immutable application images, TLS termination,
explicit persistent secrets and a private database network. Restrict monitoring and API
documentation to an operator network or an authenticated gateway. Existing monitoring
endpoints expose aggregate operational data without operator authentication.

Use a stable random `AUTH_JWT_SECRET`; the local random per-process fallback invalidates
sessions on restart. Keep `.env` outside Git, restrict its filesystem permissions, and
configure provider quotas. Do not expose ports 5434, 8010 or 5173 through a cloud firewall.

## Release and recovery

1. Build and tag an image with the Git revision. Run CI against that revision.
2. Take a PostgreSQL backup and verify restoration in a separate database.
3. Apply reviewed Alembic migrations once, before routing traffic to the new backend.
4. Verify `/health/ready`, account login, ownership isolation and a complete planning turn.
5. Inspect model errors, fallback rates and unpriced calls. A missing tariff is unknown
   cost, not zero cost. Compare quality only for matching dataset and prompt versions.
6. Roll back the application image on failure. A schema rollback requires a reviewed
   compatibility plan; dropping tables is not a recovery strategy.

Persistent volumes are not backups. `make clean` deletes the database volume.

## Remaining acceptance gates

Real supplier inventory, booking, load SLOs and relevance-labelled retrieval metrics
require separate acceptance. A synthetic catalogue cannot prove supplier availability.
The existing number guard and provenance checks reduce errors but do not prove the
truth of every generated sentence. Track factuality and failure examples explicitly.
