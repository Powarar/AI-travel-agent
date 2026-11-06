"""Idempotent indexing command. Only changed descriptions consume embedding calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from ..retrieval.catalogue import database_url
from ..retrieval.hybrid import AsyncEmbedder, query_terms
from ..retrieval.index_text import AMENITY_RU


async def rebuild() -> dict[str, int | str]:
    conn = await asyncpg.connect(database_url())
    updated = 0
    embedder = AsyncEmbedder()
    model = embedder.model or "lexical-only"
    try:
        # One indexer per model; changed hashes are checked again on every run.
        await conn.execute("SELECT pg_advisory_lock(hashtext($1))", "search-index:" + model)
        rows = await conn.fetch("SELECT h.*, d.name AS destination_name FROM hotels h "
                                "JOIN destinations d ON d.code=h.dest_code ORDER BY h.hotel_id")
        pending: list[tuple[Any, ...]] = []
        known = {row["entity_id"]: row["content_hash"] for row in await conn.fetch(
            "SELECT entity_id, content_hash FROM search_documents WHERE model=$1", model)}
        for row in rows:
            document = " ".join([row["name"], row["destination_name"], row["address"] or "",
                                  *(AMENITY_RU.get(a, a) for a in row["amenities"] or [])])
            if row.get("description"):
                document += " " + row["description"]
            digest = hashlib.sha256(document.encode()).hexdigest()
            if known.get(row["hotel_id"]) == digest:
                continue
            # PostgreSQL performs the same Russian stemming for documents and queries.
            lexemes = await conn.fetch(
                "SELECT unnest(lexemes) AS term FROM ts_debug('russian', $1) WHERE lexemes IS NOT NULL", document)
            terms: Counter[str] = Counter(row["term"] for row in lexemes)
            pending.append((row["hotel_id"], model, digest, document, json.dumps(terms), sum(terms.values())))
        for offset in range(0, len(pending), 32):
            batch = pending[offset:offset + 32]
            vectors = await embedder.embed([row[3] for row in batch]) if embedder.model else []
            await conn.executemany(
                "INSERT INTO search_documents(entity_id,model,content_hash,document,terms,doc_length,embedding) "
                "VALUES($1,$2,$3,$4,$5::jsonb,$6,$7::vector) ON CONFLICT(entity_id,model) DO UPDATE SET "
                "content_hash=EXCLUDED.content_hash,document=EXCLUDED.document,terms=EXCLUDED.terms,"
                "doc_length=EXCLUDED.doc_length,embedding=EXCLUDED.embedding,updated_at=now()",
                [(*row, json.dumps(vectors[index]) if vectors else None) for index, row in enumerate(batch)],
            )
            updated += len(batch)
            print(json.dumps({"indexed": updated, "pending": len(pending), "model": model}), flush=True)
    finally:
        await conn.close()
    return {"updated": updated, "model": model}


if __name__ == "__main__":
    from services.agent.app.llm.provider_openai_compatible import load_env_file
    load_env_file()
    print(json.dumps(asyncio.run(rebuild()), ensure_ascii=False))
