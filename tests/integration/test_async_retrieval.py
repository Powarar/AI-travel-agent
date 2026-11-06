"""SQL BM25 matches the reference formula and cannot escape the admissible IDs."""
from __future__ import annotations

import json
import os

import asyncpg
import pytest

from services.backend.app.retrieval.catalogue import database_url
from services.backend.app.retrieval.hybrid import bm25, lexical_search, query_terms


@pytest.mark.asyncio
async def test_postgres_bm25_parity_and_admissible_set() -> None:
    try:
        conn = await asyncpg.connect(database_url())
    except (OSError, asyncpg.PostgresError):
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            raise
        pytest.skip("Postgres is not available")
    try:
        try:
            rows = await conn.fetch("SELECT entity_id, terms FROM search_documents WHERE model=$1",
                                    "qwen3-embedding-8b")
        except asyncpg.UndefinedTableError:
            if os.environ.get("REQUIRE_TEST_DB") == "1":
                raise
            pytest.skip("Search migrations have not been applied")
        if not rows:
            pytest.skip("No indexed development catalogue")
        documents = {row["entity_id"]: json.loads(row["terms"]) for row in rows}
        terms = await query_terms(conn, "бассейн тихий отель")
        allowed = list(documents)[:15]
        scores = await lexical_search(conn, "qwen3-embedding-8b", terms, allowed)
        reference = bm25(terms, documents)
        assert set(scores) <= set(allowed)
        assert scores == pytest.approx({key: reference[key] for key in allowed if reference[key] > 0})
        assert await lexical_search(conn, "qwen3-embedding-8b", terms, []) == {}
    finally:
        await conn.close()
