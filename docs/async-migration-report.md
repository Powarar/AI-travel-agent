# Async migration verification — 2026-11-05

The default HTTP executor now awaits model, embedding, rerank and database I/O.
The planner has no global execution lock. Same-trip changes remain serialized by
PostgreSQL row locking. Independent dialogues are covered by a barrier-based
concurrency test rather than an unreliable elapsed-time assertion.

## Verified behavior

- Parse and question slots create a validated `TripState`, followed by structured
  source arguments and only the required database services.
- The Qwen embedding gateway accepted `dimensions=1024`. Two probe embeddings had
  exactly 1024 components. The database contains 1,318 indexed hotel documents.
- A second complete indexing run returned `updated: 0` without embedding requests.
- SQL BM25 scores match the Python reference formula. Retrieval cannot return an
  ID outside the admissible hotel set. Cosine distances are computed in pgvector;
  rankings are fused with equal-weight RRF, constant 60.
- Up to twelve deterministically validated packages enter final rerank. Three
  options are persisted in `TripState` and shown alongside the chosen package.
- The Russian final response can compare these options and return follow-ups.
- A flight-only follow-up calls `transport_search` and `transfer_search`; it does
  not call hotel search or destination search. Prior pool and party requirements
  survive the follow-up and process restart.

## Live HTTP acceptance

Latest measured run: five cases, five correct functional outcomes. Three cases
completed without any provider error. The strict live command intentionally
returned exit code 1: the primary reranker failed in one case and used its Lite
fallback; Parse had a provider failure in the unknown-city case and used its
deterministic fallback. These are reported failures, not credited as successful
model calls.

| Slot/model | Calls | p50 ms | Sample p95 ms | Errors |
| --- | ---: | ---: | ---: | ---: |
| Parse / deepseek-v4.1-flash-1017 | 5 | 3082 | 3929 | 1 |
| Questions / deepseek-v4.1-flash-1017 | 1 | 1989 | 1989 | 0 |
| Rerank / qwen3-reranker-8b | 3 | 1748 | 1873 | 1 |
| Rerank fallback / rerank-3-lite | 1 | 2761 | 2761 | 0 |
| Explain / qwen3.7-plus | 3 | 5241 | 5876 | 0 |

Query embeddings came from the database cache in this last run. An earlier
uncached query took 6704 ms; another timed out at 20 seconds. The original
20-second total budget was insufficient for that combination of external calls.
The HTTP budget is now an explicit configurable 45 seconds, query vectors are
cached, and embedding failures are exposed as lexical-only retrieval.

These samples do not establish model accuracy or production p95. The primary
reranker did not meet the requested one-second latency target in these samples;
Lite also exceeded one second. A relevance-labelled retrieval benchmark is still
needed to compare quality before changing the primary model.

The machine-readable report is `artifacts/async-e2e.json`; previous runs are
archived beside it. They are runtime artifacts and intentionally ignored by Git.
`/v1/monitoring/models` aggregates actual persisted call spans across restarts.

## Remaining boundaries

- The offers used in acceptance are synthetic catalogue data, not bookable supplier
  inventory. Existing transport conversion still synthesizes a return leg; real
  supplier adapters must provide actual outbound and return offers.
- Catalogue hotel capacity is currently unknown; transfer capacity is known. This
  patch does not turn missing room occupancy information into verified occupancy.
- Atomic selection of an alternative by ID, booking and supplier parser workers
  are not implemented by this migration. The interface displays comparisons and
  supports natural-language follow-up replanning.
- Six new offline regressions cover concurrency, cancellation cleanup, restart
  replanning, constraint retention, BM25 and rank fusion. A PostgreSQL integration
  test verifies SQL BM25 parity and admissible-set behavior.
- The existing 15-case offline evaluation passed. Frontend dependency installation
  and the production build passed. The full suite and type checks were run.
