"""BM25 + semantic retrieval over admissible IDs using weighted reciprocal rank fusion."""
from __future__ import annotations

import json
import hashlib
import math
import os
import time
from typing import Any

import httpx
from shared.telemetry import model_spans
from shared.model_pricing import price_span


class AsyncEmbedder:
    def __init__(self) -> None:
        self.model = os.environ.get("EMBEDDING_MODEL", "").strip()
        self.dimensions = 1024
        self.url = os.environ.get("EMBEDDING_BASE_URL", os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("LLM_API_KEY", "")
        self.tokens_used = 0
        self._usage: dict[str, Any] = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        started = time.monotonic()
        status, error = "cancelled", None
        try:
            vectors = await self._embed(texts)
            status = "ok"
            return vectors
        except Exception as exc:
            status, error = "error", type(exc).__name__
            raise
        finally:
            sink = model_spans.get()
            if sink is not None:
                span = {"slot": "embedding", "model": self.model, "dimensions": self.dimensions,
                             "latency_ms": round((time.monotonic() - started) * 1000), "status": status,
                             "error": error, "input_tokens": self._usage.get("prompt_tokens"), "output_tokens": 0,
                             "total_tokens": self._usage.get("total_tokens")}
                span.update(price_span(span))
                sink.append(span)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not self.model or not self.url or not self.key:
            raise ValueError("EMBEDDING_MODEL, BASE_URL and API_KEY must be configured")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.url + "/embeddings", headers={"Authorization": f"Bearer {self.key}"},
                                         json={"model": self.model, "input": texts, "dimensions": self.dimensions})
            response.raise_for_status()
            payload = response.json()
            usage = payload.get("usage") or {}
            self._usage = usage
            self.tokens_used += int(usage.get("total_tokens") or 0)
            rows = sorted(payload["data"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError("embedding response does not cover the input exactly")
            vectors = [[float(v) for v in row["embedding"]] for row in rows]
            if not vectors or not vectors[0] or any(len(v) != self.dimensions or
                    not all(math.isfinite(x) for x in v) for v in vectors):
                raise ValueError("invalid embedding vectors")
            return vectors


def bm25(query: list[str], documents: dict[str, dict[str, int]], *, k1: float = 1.2,
         b: float = 0.75) -> dict[str, float]:
    """Robertson BM25; this lexical score is never confused with cosine similarity."""
    n = len(documents)
    if not n:
        return {}
    avgdl = sum(sum(terms.values()) for terms in documents.values()) / n or 1
    scores = dict.fromkeys(documents, 0.0)
    for term in set(query):
        df = sum(term in terms for terms in documents.values())
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for key, terms in documents.items():
            tf = terms.get(term, 0)
            if tf:
                scores[key] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * sum(terms.values()) / avgdl))
    return scores


def fuse(lexical: dict[str, float], semantic: dict[str, float], *, weight: float = 0.5,
         rank_constant: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for lane, factor in ((lexical, 1 - weight), (semantic, weight)):
        for rank, (key, _) in enumerate(sorted(lane.items(), key=lambda row: (-row[1], row[0])), start=1):
            scores[key] = scores.get(key, 0) + factor / (rank_constant + rank)
    return scores


async def query_terms(conn: Any, text: str) -> list[str]:
    return list(await conn.fetchval("SELECT tsvector_to_array(to_tsvector('russian', $1))", text))


async def lexical_search(conn: Any, model: str, terms: list[str], admissible: list[str]) -> dict[str, float]:
    rows = await conn.fetch("""
        WITH corpus AS MATERIALIZED (
            SELECT entity_id, terms, doc_length FROM search_documents WHERE model=$1
        ), stats AS (
            SELECT count(*)::float8 AS n, greatest(avg(doc_length), 1)::float8 AS avgdl FROM corpus
        ), query_terms AS (
            SELECT DISTINCT unnest($2::text[]) AS term
        ), frequencies AS (
            SELECT q.term, count(c.entity_id)::float8 AS df FROM query_terms q
            LEFT JOIN corpus c ON c.terms ? q.term GROUP BY q.term
        )
        SELECT c.entity_id, sum(
            ln(1 + (s.n - f.df + 0.5) / (f.df + 0.5)) *
            (c.terms->>f.term)::float8 * 2.2 /
            ((c.terms->>f.term)::float8 + 1.2 * (0.25 + 0.75 * c.doc_length / s.avgdl))
        ) AS score
        FROM corpus c CROSS JOIN stats s JOIN frequencies f ON c.terms ? f.term
        WHERE c.entity_id=ANY($3::text[])
        GROUP BY c.entity_id ORDER BY score DESC, c.entity_id
    """, model, terms, admissible)
    return {row["entity_id"]: float(row["score"]) for row in rows}


async def hybrid_search(conn: Any, query: str, admissible: list[str]) -> tuple[dict[str, float], str]:
    if not query.strip() or not admissible:
        return {}, "not_requested"
    embedder = AsyncEmbedder()
    model = embedder.model or "lexical-only"
    lexical = await lexical_search(conn, model, await query_terms(conn, query), admissible)
    semantic: dict[str, float] = {}
    status = "lexical_only:embedding_not_configured"
    if embedder.model:
        try:
            digest = hashlib.sha256(query.encode()).hexdigest()
            cached = await conn.fetchval("SELECT embedding::text FROM query_embeddings "
                                         "WHERE model=$1 AND content_hash=$2 AND created_at > now() - interval '1 day'",
                                         embedder.model, digest)
            if cached:
                vector = json.loads(cached)
            else:
                [vector] = await embedder.embed([query])
                await conn.execute(
                    "INSERT INTO query_embeddings(model,content_hash,embedding) VALUES($1,$2,$3::vector) "
                    "ON CONFLICT(model,content_hash) DO UPDATE SET embedding=EXCLUDED.embedding,created_at=now()",
                    embedder.model, digest, json.dumps(vector),
                )
            hits = await conn.fetch(
                "SELECT entity_id, 1 - (embedding <=> $1::vector) AS score FROM search_documents "
                "WHERE model=$2 AND entity_id=ANY($3::text[]) AND embedding IS NOT NULL",
                json.dumps(vector), embedder.model, admissible,
            )
            semantic = {row["entity_id"]: float(row["score"]) for row in hits if row["score"] is not None}
            status = "hybrid" if semantic else "lexical_only:no_indexed_vectors"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            status = "lexical_only:embedding_provider_error"
    return fuse(lexical, semantic), status
