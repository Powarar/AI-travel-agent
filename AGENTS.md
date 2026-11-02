# AI Travel Agent — contributor guide

## Orientation

This is a multi-service travel-planning prototype. `shared/contracts/` owns the
domain vocabulary. The backend owns persistence and catalogue access; the agent
owns planning and validation; the web app is only a consumer of the API.

Read the nearest `pyproject.toml`, `package.json`, and tests before changing a
component. Prefer a focused change over a cross-service refactor.

## Non-negotiable domain rules

- `TripState` is the sole source of truth. Message history may produce a delta, not
  a parallel state machine.
- Hard constraints (budget, dates, occupancy, pets, availability, and transport
  feasibility) may not be relaxed implicitly.
- Financial values use the shared `Money`/`Decimal` types. Never use `float` for
  prices, totals, or budget comparisons.
- A catalogue entity is not an offer. Offers and externally sourced facts must carry
  provenance (`source_id`, freshness, and data status).
- Deterministic code validates facts, dates, totals, and constraints. An LLM may
  parse intent, ask concise questions, rank already admissible candidates, and write
  explanations; it must not invent facts or make authoritative calculations.

## Boundaries

- Keep shared contract changes backwards-compatible when possible and update all
  affected services and tests in the same change.
- `services/agent/app/validation/` must remain free of LLM and network imports.
- Keep provider-specific logic behind the existing client/provider interfaces.
- Do not add secrets, datasets generated at runtime, build output, caches, or
  dependency directories to Git.

## Verification

Run the smallest relevant test first, then the appropriate repository checks:

```bash
make setup
make test
make lint
```

For frontend work, run `npm ci` and `npm run build` inside `web/`. For changes that
touch persistence or API wiring, bring up the stack with `make up` and verify the
affected endpoint. State clearly what was run and what was not.
