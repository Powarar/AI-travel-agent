"""Замер retrieval на каталоге: python -m services.backend.app.retrieval --dest sochi ..."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import date
from decimal import Decimal

import asyncpg  # type: ignore[import-untyped]

from services.backend.app.ranking import rank

from .embeddings import EmbeddingIndex, HashingEmbedder
from .hard_filter import HardFilter, fetch_candidates
from .index_text import index_text


async def _main(args: argparse.Namespace) -> None:
    f = HardFilter(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        guests=args.guests,
        destination=args.dest,
        country=args.country,
        stars_min=args.stars_min,
        budget_per_night_max=Decimal(args.max_night) if args.max_night else None,
        must=tuple(args.must),
        pets=args.pets,
    )
    dsn = os.environ.get("DATABASE_URL", "postgresql://ostrovok:ostrovok@localhost:5434/ostrovok")
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        t0 = time.perf_counter()
        candidates = await fetch_candidates(conn, f)
        t_sql = time.perf_counter() - t0
    finally:
        await conn.close()
    t1 = time.perf_counter()
    index = EmbeddingIndex(HashingEmbedder())
    index.upsert((c.hotel_id, index_text(c)) for c in candidates)
    ranked = rank(candidates, index.similarity(args.query, [c.hotel_id for c in candidates]))
    t_rank = time.perf_counter() - t1

    print(f"кандидатов после SQL-фильтра: {len(candidates)}")
    print(f"wall-clock: SQL {t_sql * 1000:.1f} мс, эмбеддинги+ранжирование {t_rank * 1000:.1f} мс")
    for r in ranked[: args.top]:
        parts = ", ".join(f"{k}={v:.3f}" for k, v in r.contributions.items())
        print(f"{r.score:.3f}  {r.hotel.hotel_id:<24} {r.hotel.total_price} {r.hotel.currency}  [{parts}]")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-11-14")
    p.add_argument("--end", default="2026-11-21")
    p.add_argument("--guests", type=int, default=2)
    p.add_argument("--dest")
    p.add_argument("--country")
    p.add_argument("--stars-min", type=int)
    p.add_argument("--max-night")
    p.add_argument("--must", nargs="*", default=[])
    p.add_argument("--pets", action="store_true")
    p.add_argument("--query", default="")
    p.add_argument("--top", type=int, default=5)
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
