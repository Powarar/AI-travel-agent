"""Общий слой сид-пайплайна: HTTP с кэшем, детерминированная «случайность», реестр источников.

Stdlib-only: пайплайн должен запускаться на любой машине команды без pip-install.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / ".cache"
SEEDS_DIR = ROOT / "data" / "seeds"

USER_AGENT = "ai-travel-agent-seed/1.0"
BACKOFF_S = (1.0, 3.0, 9.0)

# Единая точка истины по происхождению данных: real | synthetic.
_SOURCES: dict[str, dict[str, str]] = {}


def source(source_id: str, name: str, url: str, kind: str) -> None:
    row = {
        "source_id": source_id,
        "name": name,
        "url": url,
        "kind": kind,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        previous = next(
            (entry for entry in json.loads((SEEDS_DIR / "sources.json").read_text())
             if entry["source_id"] == source_id),
            None,
        )
    except FileNotFoundError:
        previous = None
    if previous is not None and all(
        previous.get(key) == row[key] for key in ("source_id", "name", "url", "kind")
    ):
        row["fetched_at"] = previous["fetched_at"]
    _SOURCES[source_id] = row


def registered_sources() -> list[dict[str, str]]:
    return [
        {**meta, "source_id": sid}
        for sid, meta in sorted(_SOURCES.items())
    ]


def http_get(url: str, timeout: int = 120, retries: int = 3) -> bytes:
    """GET с disk-кэшем: повторные прогоны офлайн и байт-в-байт одинаковые."""
    key = hashlib.sha1(url.encode()).hexdigest()
    hit = CACHE_DIR / key
    if hit.exists():
        return hit.read_bytes()
    last: Exception | None = None
    for wait in BACKOFF_S[:retries]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = hit.with_suffix(".part")
            tmp.write_bytes(payload)
            tmp.rename(hit)
            return payload
        except Exception as exc:  # network на этой машине нестабилен — ретраим
            last = exc
            time.sleep(wait)
    raise RuntimeError(f"http_get: источник недоступен {url}: {last}") from last


def http_get_optional(url: str, timeout: int = 120, retries: int = 2) -> bytes | None:
    """Как http_get, но отказ источника не валит прогон: сид остаётся на синтетике."""
    try:
        return http_get(url, timeout=timeout, retries=retries)
    except RuntimeError:
        print(f"  ! источник недоступен, пропускаю: {url[:120]}")
        return None


# --- детерминированная «случайность»: один key → одно число навсегда ---

def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def unit(key: str) -> float:
    return int(_digest(key)[:16], 16) / float(1 << 64)


def randint(key: str, low: int, high: int) -> int:
    return low + int(_digest(key)[:8], 16) % (high - low + 1)


def pick(key: str, seq: list):
    return seq[int(_digest(key)[:8], 16) % len(seq)]


def sample(key: str, seq: list, k: int) -> list:
    """Детерминированная выборка без замены, порядок — от key."""
    pool = list(seq)
    out = []
    for i in range(k):
        idx = int(_digest(f"{key}|{i}")[:8], 16) % len(pool)
        out.append(pool.pop(idx))
    return out


def jitter(key: str, spread: float) -> float:
    """Множитель 1±spread."""
    return 1.0 + (unit(key) * 2 - 1) * spread


def write_seed(name: str, rows: Any, compact: bool = False) -> Path:
    """JSON-массив. compact=True — по одной строке на запись (для десятков тысяч строк)."""
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    path = SEEDS_DIR / name
    if isinstance(rows, dict) and compact:
        raise ValueError("compact только для списков")
    if name != "sources.json" and isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("source_id"):
                source_meta = _SOURCES.get(row["source_id"])
                if source_meta is None:
                    raise ValueError(f"неизвестный source_id: {row['source_id']}")
                row["fetched_at"] = source_meta["fetched_at"]
                row["synthetic"] = row.get("data_status") == "synthetic"
    if compact:
        body = ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        text = "[\n" + body + "\n]\n" if body else "[]\n"
    else:
        text = json.dumps(rows, ensure_ascii=False, indent=1) + "\n"
    path.write_text(text)
    n = len(rows) if isinstance(rows, (list, dict)) else 0
    print(f"  → data/seeds/{name}: {n} строк, {path.stat().st_size/1e6:.2f} МБ")
    return path


def read_seed(name: str) -> list[dict[str, Any]]:
    return json.loads((SEEDS_DIR / name).read_text())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def chunked(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
