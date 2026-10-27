"""Реестр инструментов (T-04): поле → инструмент, политика ретраев, content-hash кэш.

Словарь-реестр, а не DSL (ADR-004): `guard` — обычная функция, её можно поставить
брейкпойнтом. Решение «какие инструменты грязные» принимает код по реверс-индексу полей
(`orchestration.graph`), модель его не угадывает.

Пути полей — те же, что отдаёт `TripRequest.iter_fields()`, плюс производные, которые
считает код, а не парсит из текста:

* `dates.month` — месяц выезда; сдвиг даты внутри месяца не делает грязным поиск
  направлений и погоды, только перелёты и отели;
* `destinations` — результат `suggest_destinations`, от него зависят поиск отелей и перелётов;
* `transport.arrival_time` — результат `transport_search`, от него зависит трансфер;
* `basket` — подтверждённый пользователем выбор, нужен только checkout.

`budget.amount` подписан на подбор направлений, но не на поиск отелей: общая сумма — забота
валидатора (`BudgetLedger` в T-03), а не параметр запроса к источнику. В источнике работает
только `lodging.budget_per_night_max`. Если бы сумма была во `inputs` отелей, ступень 0 на
«бюджет −30%» ложно объявляла бы отельный поиск грязным.

`guard_fields` — то, что читает функция `guard`, а не то, что уходит в запрос к источнику.
Грязный инструмент считается по `watched` = `inputs` + `guard_fields`: пересобранное поле
меняет либо запрос к источнику, либо готовность запуска. Без `guard_fields`
`direction.explicit` («я еду в Сочи» вместо «не знаю») не сделал бы грязными перелёты и
отели — в запросе к источнику этого поля нет, а готовность поиска определяет именно оно.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from shared.contracts.state import TripState

Cost = Literal["none", "low", "med", "high"]
Fallback = Literal["stale_cache", "default_estimate", "none"]


@dataclass(frozen=True)
class Tool:
    name: str
    inputs: frozenset[str]
    provides: frozenset[str]
    guard_fields: frozenset[str]
    output: str
    cost: Cost
    guard: Callable[[TripState], bool]
    retries: int = 0
    backoff: tuple[float, ...] = ()
    jitter: bool = False
    timeout_s: float | None = None
    breaker_failures: int | None = None
    breaker_window_s: int | None = None
    fallback: Fallback = "none"
    cache_ttl_s: float | None = None

    @property
    def watched(self) -> frozenset[str]:
        """Поля, пересборка которых делает инструмент грязным: и запрос, и готовность."""

        return self.inputs | self.guard_fields

    def ready(self, state: TripState) -> bool:
        return self.guard(state)


def _always(state: TripState) -> bool:
    return True


def _destination_known(state: TripState) -> bool:
    return bool(state.candidates.destinations) or not state.request.direction.explicit.is_unknown


def _origin_and_destination(state: TripState) -> bool:
    return not state.request.origin.is_unknown and _destination_known(state)


def _dates_and_destination(state: TripState) -> bool:
    dates = state.request.dates
    return _destination_known(state) and not dates.start.is_unknown and not dates.end.is_unknown


def _transport_landed(state: TripState) -> bool:
    return bool(state.candidates.transport)


def _has_both_sides(state: TripState) -> bool:
    return bool(state.candidates.transport and state.candidates.hotels)


def _basket_confirmed(state: TripState) -> bool:
    return bool(state.chosen.items)


REGISTRY: dict[str, Tool] = {
    "suggest_destinations": Tool(
        name="suggest_destinations",
        inputs=frozenset({
            "direction.mode", "direction.explicit", "direction.excluded", "dates.month",
            "themes", "transport.max_hours_direct", "budget.amount", "budget.includes",
            "party.adults",
        }),
        provides=frozenset({"destinations"}),
        output="candidates.destinations",
        cost="med",
        guard=_always,
        guard_fields=frozenset(),
        retries=2, backoff=(0.5, 1.0), jitter=True, timeout_s=2.0,
        fallback="stale_cache", cache_ttl_s=86_400,
    ),
    "weather_climate": Tool(
        name="weather_climate",
        inputs=frozenset({"direction.explicit", "destinations", "dates.month", "themes"}),
        provides=frozenset({"climate"}),
        output="candidates.destinations[].climate",
        cost="low",
        guard=_destination_known,
        guard_fields=frozenset({"direction.explicit", "destinations"}),
        retries=1, backoff=(0.5,), timeout_s=1.5,
        fallback="stale_cache", cache_ttl_s=604_800,
    ),
    "transport_search": Tool(
        name="transport_search",
        inputs=frozenset({
            "origin", "destinations", "dates.start", "dates.end", "dates.month",
            "party.adults", "party.children", "party.pets",
            "transport.modes", "transport.max_hours_direct", "transport.prefer_direct",
            "transport.overnight_ok", "budget.includes",
        }),
        provides=frozenset({"transport.arrival_time"}),
        output="candidates.transport",
        cost="high",
        guard=_origin_and_destination,
        guard_fields=frozenset({"origin", "direction.explicit", "destinations"}),
        retries=3, backoff=(0.5, 1.0, 2.0), jitter=True, timeout_s=3.0,
        breaker_failures=3, breaker_window_s=60, fallback="stale_cache", cache_ttl_s=900,
    ),
    "hotel_search": Tool(
        name="hotel_search",
        inputs=frozenset({
            "destinations", "dates.start", "dates.end", "nights",
            "party.adults", "party.children", "party.pets",
            "lodging.rooms", "lodging.stars_min", "lodging.budget_per_night_max", "lodging.must",
            "soft_preferences", "budget.includes",
        }),
        provides=frozenset({"hotels"}),
        output="candidates.hotels",
        cost="high",
        guard=_dates_and_destination,
        guard_fields=frozenset({"direction.explicit", "destinations", "dates.start", "dates.end"}),
        retries=3, backoff=(0.5, 1.0, 2.0), jitter=True, timeout_s=3.0,
        breaker_failures=3, breaker_window_s=60, fallback="stale_cache", cache_ttl_s=3_600,
    ),
    "transfer_search": Tool(
        name="transfer_search",
        inputs=frozenset({
            "destinations", "transport.arrival_time",
            "party.adults", "party.children", "party.pets",
        }),
        provides=frozenset({"transfers"}),
        output="candidates.transfers",
        cost="low",
        guard=_transport_landed,
        guard_fields=frozenset({"transport.arrival_time"}),
        retries=1, backoff=(0.5,), timeout_s=1.5,
        fallback="default_estimate", cache_ttl_s=86_400,
    ),
    "coherence_check": Tool(
        name="coherence_check",
        inputs=frozenset({"transport.arrival_time", "hotels", "transfers", "dates.start", "dates.end"}),
        provides=frozenset({"violations"}),
        output="violations",
        cost="none",
        guard=_has_both_sides,
        guard_fields=frozenset({"transport.arrival_time", "hotels", "transfers"}),
    ),
    "checkout_mock": Tool(
        name="checkout_mock",
        inputs=frozenset({"basket"}),
        provides=frozenset({"order"}),
        output="order_id",
        cost="none",
        guard=_basket_confirmed,
        guard_fields=frozenset({"basket"}),
        retries=0, timeout_s=2.0,
    ),
}

TOOL_NAMES: tuple[str, ...] = tuple(REGISTRY)


def args_hash(tool: Tool, args: Mapping[str, object]) -> str:
    """Стабильный content-hash по аргументам: одно и то же обращение к источнику — один раз.

    Ключ включает имя инструмента: у `budget.amount=150000` в разных инструментах разные
    наборы срезов, и переиспользовать результат между ними нельзя.
    """

    payload = json.dumps(
        {"tool": tool.name, "args": args},
        default=_stringify,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _stringify(value: object) -> str:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return json.dumps(dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return str(value)


class ToolCache:
    """Кэш по content-hash + счётчик обращений к источнику (для приёмки и трейса)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, object, float | None]] = {}
        self.source_calls = 0
        self.cache_hits = 0

    def call(self, tool: Tool, args: Mapping[str, object], fetch: Callable[[], object],
             *, now_s: float = 0.0) -> tuple[object, bool]:
        key = args_hash(tool, args)
        entry = self._entries.get(key)
        if entry is not None:
            _, value, expires = entry
            if expires is None or now_s < expires:
                self.cache_hits += 1
                return value, True

        self.source_calls += 1
        value = fetch()
        expires_at = None if tool.cache_ttl_s is None else now_s + tool.cache_ttl_s
        self._entries[key] = (tool.name, value, expires_at)
        return value, False

    def invalidate(self, tools: Iterable[str]) -> int:
        """Ступень 2: выбрасываем только грязные инструменты, остальное остаётся тёплым."""

        dirty = set(tools)
        stale = [key for key, (name, _, _) in self._entries.items() if name in dirty]
        for key in stale:
            self._entries.pop(key)
        return len(stale)

    def size(self) -> int:
        return len(self._entries)
