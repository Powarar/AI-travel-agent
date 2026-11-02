# AI Travel Agent

[Русская версия](README.ru.md)

An AI-assisted trip-planning application that turns a conversational request into a
traceable, constraint-aware travel plan. It keeps language understanding separate
from deterministic planning: the model extracts intent and explains choices, while
code owns prices, availability, validation, and the final decision.

> **Project status:** active prototype. The catalogue is seeded for local development;
> it is not a booking engine and must not be presented as one.

## What it demonstrates

- A stateful planning flow: request → clarifying questions → search → plan → checkout mock.
- Hard-constraint validation for dates, occupancy, pets, transfers, availability, and budget.
- Incremental replanning: a change invalidates only the affected parts of a trip.
- Clear provenance: offers carry source, freshness, and verification status.
- An LLM boundary that keeps arithmetic and factual assertions out of the model.
- A React/Vite client, FastAPI services, PostgreSQL/pgvector, and a Telegram bot adapter.

## Architecture

```
web / Telegram bot
        │
        ▼
   backend API ───────── PostgreSQL + pgvector
        │
        ▼
  planning agent ─────── catalog and provider adapters
        │
        ▼
 shared domain contracts
```

The repository is organized by deployable component:

| Path | Responsibility |
| --- | --- |
| `web/` | React + TypeScript user interface |
| `services/backend/` | Public API, persistence, search, ranking, and integrations |
| `services/agent/` | Planning graph, validation, repair, prompts, and tool orchestration |
| `services/bot/` | Telegram adapter |
| `shared/contracts/` | Typed domain models shared by services |
| `data/` | Reproducible seed data and generators |
| `tests/` | Unit, integration, agent, and end-to-end scenario tests |

## Quick start

### Prerequisites

- Docker Desktop with Docker Compose
- Python 3.11+ for local tests and data scripts
- Node.js 22+ only when running the frontend outside Docker

```bash
cp .env.example .env
make setup
make up
make seed
make test
```

The API is exposed on `http://localhost:8010`; the development UI is available on
`http://localhost:5173` after `make web` (or as part of `make up`).

The default `LLM_PROVIDER=mock` is deterministic and needs no API key. To use an
OpenAI-compatible provider, set `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_BASE_URL`, and
the relevant model variables in `.env`. Never commit `.env` or private keys.

## Everyday commands

```bash
make setup    # create the Python development environment
make up       # start the local stack and run migrations
make down     # stop the stack
make seed     # load the development catalogue
make test     # run the Python test suite
make lint     # run type checks and architecture guardrails
make eval     # run offline evaluation scenarios
make web      # run the frontend in Docker with hot reload
```

## Development principles

1. `TripState` is the source of truth; chat history only produces requirement changes.
2. Hard constraints are never silently weakened. Return alternatives or ask for an
   explicit trade-off instead.
3. Use `Decimal`-based domain money types—never floats—for price calculations.
4. Treat catalogue data and offers as separate concerns, and preserve provenance.
5. Keep LLM calls behind the provider interface. Validation, totals, dates, and facts
   stay deterministic.

Implementation guidance for coding agents lives in [AGENTS.md](AGENTS.md).

## Repository hygiene

Generated dependencies, Python caches, build output, local evaluation output, and
developer secrets are intentionally ignored. Recreate them locally with `npm ci`,
the Make targets above, or the relevant generator; do not commit them.
