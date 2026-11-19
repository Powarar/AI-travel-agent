"""Точка входа сид-пайплайна: реальные источники → синтетика поверх них → data/seeds/*.json.

Порядок важен: weather даёт климатический сезонный коэффициент для hotels; routes требует
hotels+gateways. Прогон полностью детерминирован при одном и том же кэше (data/.cache):
два запуска подряд дают байт-в-байт одинаковые файлы.

Запуск:  python3 data/generators/run_all.py [шаг ...]
Шаги:    destinations airports weather hotels flights routes
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# даём «python3 data/generators/run_all.py» работать из любой папки:
# репо-корень должен быть в sys.path для пакетов data.generators.*
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import data.generators.airports as airports  # noqa: E402 — sys.path.insert выше
import data.generators.common as common  # noqa: E402 — sys.path.insert выше
import data.generators.destinations as destinations  # noqa: E402 — sys.path.insert выше
import data.generators.flights as flights  # noqa: E402 — sys.path.insert выше
import data.generators.hotels as hotels  # noqa: E402 — sys.path.insert выше
import data.generators.routes as routes  # noqa: E402 — sys.path.insert выше
import data.generators.weather as weather  # noqa: E402 — sys.path.insert выше

STEPS = {
    "destinations": destinations.run,
    "airports": airports.run,
    "weather": weather.run,
    "hotels": hotels.run,
    "flights": flights.run,
    "routes": routes.run,
}


def main() -> int:
    wanted = sys.argv[1:] or list(STEPS)
    t0 = time.time()
    for name in wanted:
        if name not in STEPS:
            print(f"неизвестный шаг: {name}")
            return 2
        print(f"== {name} ==")
        t = time.time()
        STEPS[name]()
        print(f"   {time.time() - t:.1f}s")
    # sources.json — накопительный: частичный прогон (например, только routes)
    # не должен терять источники из предыдущих шагов.
    merged = {}
    try:
        for row in common.read_seed("sources.json"):
            merged[row["source_id"]] = row
    except FileNotFoundError:
        pass
    for row in common.registered_sources():
        merged[row["source_id"]] = row
    common.write_seed("sources.json", [merged[k] for k in sorted(merged)])
    print(f"== готово за {time.time() - t0:.1f}s ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
