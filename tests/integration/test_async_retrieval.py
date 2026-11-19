"""SQL BM25 matches the reference formula and cannot escape the admissible IDs.

The corpus is seeded by the test under its own model name and rolled back afterwards,
so the parity check also runs on a fresh CI database: BM25 statistics (n, avgdl, df)
are computed over ``WHERE model=$1``, therefore a private model isolates the fixture
from the development catalogue instead of skipping when that catalogue is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter

import asyncpg
import pytest

from services.backend.app.retrieval.catalogue import database_url
from services.backend.app.retrieval.hybrid import bm25, lexical_search, query_terms

MODEL = "bm25-parity-fixture"
DOCUMENTS = (
    "Тихий отель у моря, бассейн и спа",
    "Отель с бассейном, шумная дискотека до утра",
    "Городской отель, тихий двор и парковка",
    "Курортный отель: бассейн, море, спа",
    "Тихий гостевой дом в горах без бассейна",
    "Отель у моря, спа и бассейн для детей",
)


async def _seed(conn: asyncpg.Connection) -> dict[str, dict[str, int]]:
    documents: dict[str, dict[str, int]] = {}
    for index, document in enumerate(DOCUMENTS):
        hotel_id = f"bm25-parity-{index}"
        lexemes = await conn.fetch(
            "SELECT unnest(lexemes) AS term FROM ts_debug('russian', $1) WHERE lexemes IS NOT NULL",
            document)
        terms = Counter(row["term"] for row in lexemes)
        await conn.execute(
            "INSERT INTO hotels(hotel_id, fetched_at, synthetic) VALUES($1, now(), true) "
            "ON CONFLICT (hotel_id) DO NOTHING", hotel_id)
        await conn.execute(
            "INSERT INTO search_documents(entity_id, model, content_hash, document, terms, doc_length) "
            "VALUES($1, $2, $3, $4, $5::jsonb, $6) "
            "ON CONFLICT (entity_id, model) DO UPDATE SET content_hash=EXCLUDED.content_hash, "
            "document=EXCLUDED.document, terms=EXCLUDED.terms, doc_length=EXCLUDED.doc_length",
            hotel_id, MODEL, hashlib.sha256(document.encode()).hexdigest(), document,
            json.dumps(terms), sum(terms.values()))
        documents[hotel_id] = dict(terms)
    return documents


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
            await conn.fetchval("SELECT 1 FROM search_documents LIMIT 1")
        except asyncpg.UndefinedTableError:
            if os.environ.get("REQUIRE_TEST_DB") == "1":
                raise
            pytest.skip("Search migrations have not been applied")
        transaction = conn.transaction()
        await transaction.start()
        try:
            documents = await _seed(conn)
            terms = await query_terms(conn, "бассейн тихий отель")
            allowed = list(documents)[:4]
            scores = await lexical_search(conn, MODEL, terms, allowed)
            reference = bm25(terms, documents)
            assert scores, "fixture must produce non-zero BM25 scores"
            assert set(scores) <= set(allowed)
            assert scores == pytest.approx({key: reference[key] for key in allowed if reference[key] > 0})
            assert await lexical_search(conn, MODEL, terms, []) == {}
            best = max(reference, key=lambda key: reference[key])
            without_best = await lexical_search(conn, MODEL, terms, [k for k in documents if k != best])
            assert best not in without_best
        finally:
            await transaction.rollback()
    finally:
        await conn.close()