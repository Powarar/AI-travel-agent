"""JSON-датасет с hash-cache и честным stale fallback."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from shared.contracts.enums import DataStatus


@dataclass(frozen=True)
class IngestionRow:
    source_id: str
    fetched_at: datetime
    data_status: DataStatus
    payload: dict[str, object]


class MemoryCache:
    def __init__(self) -> None:
        self.rows: dict[str, list[IngestionRow]] = {}


class DatasetAdapter:
    """Принимает только JSON из документированного API/открытого датасета."""
    def __init__(self, source_id: str, fetch_json: Callable[[], Iterable[dict[str, object]]], cache: MemoryCache) -> None:
        self.source_id, self.fetch_json, self.cache = source_id, fetch_json, cache

    def warmup(self) -> list[IngestionRow]:
        key = self.source_id
        try:
            payload = list(self.fetch_json())
        except Exception:
            #: Отказ источника не превращает ранее подтверждённые данные в пустую выдачу:
            #: отдаём их только с честным статусом stale, который валидатор покажет warning.
            return [replace(row, data_status=DataStatus.STALE) for row in self.cache.rows.get(key, [])]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        if digest in self.cache.rows:
            return self.cache.rows[digest]
        now = datetime.now(timezone.utc)
        rows = [IngestionRow(self.source_id, now, DataStatus.REAL, row) for row in payload]
        self.cache.rows[digest] = rows
        self.cache.rows[key] = rows
        return rows
