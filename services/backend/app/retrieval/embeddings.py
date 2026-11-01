"""Эмбеддинги с кэшем по content_hash.

`HashingEmbedder` — офлайн-дефолт без сети и без новых зависимостей: мешок усечённых
слов, захешированный в фиксированную размерность. Настоящая модель подключается
реализацией `Embedder`, кэш и ранжирование от этого не меняются.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Protocol

from .index_text import content_hash

_WORD = re.compile(r"[\wё]+", re.IGNORECASE)


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    def __init__(self, dim: int = 256, stem: int = 5) -> None:
        self.dim = dim
        self.stem = stem

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in _WORD.findall(text.lower()):
            # Грубая основа: «бассейном» и «бассейн» попадают в одну корзину.
            token = word[: self.stem]
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            vec[int.from_bytes(digest, "big") % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class EmbeddingIndex:
    """Вектор пересчитывается, только когда меняется текст объекта."""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._by_hash: dict[str, list[float]] = {}
        self._hash_of: dict[str, str] = {}
        self.computed = 0

    def upsert(self, items: Iterable[tuple[str, str]]) -> None:
        pending: dict[str, str] = {}
        for key, text in items:
            digest = content_hash(text)
            self._hash_of[key] = digest
            if digest not in self._by_hash:
                pending[digest] = text
        if pending:
            vectors = self.embedder.embed(list(pending.values()))
            self._by_hash.update(zip(pending.keys(), vectors, strict=True))
            self.computed += len(pending)

    def similarity(self, query: str, keys: Iterable[str]) -> dict[str, float]:
        if not query:
            return {}
        [q] = self.embedder.embed([query])
        return {k: cosine(q, self._by_hash[self._hash_of[k]]) for k in keys if k in self._hash_of}
