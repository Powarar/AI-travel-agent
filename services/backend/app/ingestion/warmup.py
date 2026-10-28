"""CLI-заготовка: источник включается только явной конфигурацией, сети по умолчанию нет."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .dataset import DatasetAdapter, MemoryCache


def main() -> int:
    path = os.environ.get("INGESTION_DATASET_JSON", "").strip()
    if not path:
        print("INGESTION_DATASET_JSON не задан: сеть не трогали, warmup пропущен")
        return 0
    file = Path(path)
    def fetch() -> list[dict[str, object]]:
        payload = json.loads(file.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    rows = DatasetAdapter("open-dataset", fetch, MemoryCache()).warmup()
    print(f"warmup: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
