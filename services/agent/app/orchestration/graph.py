"""Зависимости дельты (T-04): реверс-индекс `поле → инструмент` и ступени эскалации.

Рантайм (T-05) получает отсюда два ответа: «что пересобирать» и «с какой ступени начинать».
Оба считает код по реестру, модель их не угадывает (`docs/LLM-BOUNDARY.md`: решение о
наборе инструментов не принимает никто вероятностный — иначе трейс хода невоспроизводим).

Инструменты с `cost="none"` (`coherence_check`, `checkout_mock`) в грязное множество не
попадают: к источнику они не ходят, а локальный пересчёт — это и есть `validate`, который
всегда downstream. Включить их в список «грязных» значило бы сказать рантайму «иди
переспроши источник», которого там нет.

Про `dates.month`: дельту по датам собирает рантайм, и если месяц выезда сдвинулся, он
кладёт в `changed` ещё и `dates.month`. Тогда грязнеют направления и климат; сдвиг даты
внутри месяца трогает только перелёты и отели.
"""

from __future__ import annotations

from collections.abc import Iterable

from shared.contracts.enums import EscalationStep
from services.agent.app.tools.registry import REGISTRY, Tool

#: Стадии, которые пересобираются при любой дельте (ARCHITECTURE.md §4).
DOWNSTREAM: frozenset[str] = frozenset({"compose", "validate"})

#: Меняют только порядок и подсчёт денег: ответ источника от них не зависит.
#: Пример из CONTRACTS.md, ступень 0: «бюджет −30%». Сюда не входят `themes`,
#: `budget.includes` и `transport.prefer_direct` — они уходят в запрос к источнику, поэтому
#: их смена грязнит поиск и начинается со ступени 2.
RERANK_ONLY: frozenset[str] = frozenset({
    "budget.amount", "budget.scope", "soft_preferences",
})

#: Кандидаты остаются пригодными, но выбранный план надо пересчитать на новых входных.
#: Пример из CONTRACTS.md, ступень 1: «на троих» — влезет ли в тот же номер.
RECOMPOSE_ONLY: frozenset[str] = frozenset({
    "party.adults", "party.children", "lodging.rooms",
})


def _hits_source(tool: Tool) -> bool:
    return tool.cost != "none"


def search_tools(changed: Iterable[str]) -> frozenset[str]:
    """Реверс-индекс + транзитивное замыкание вниз.

    Грязный инструмент — тот, чей запрос к источнику меняется или чья готовность к запуску
    от этого поля зависит (`watched` = `inputs` + `guard_fields`). Замыкание вниз идёт по
    `provides`: пересобранный результат (`transport.arrival_time` у трансфера) тянет за
    собой всех, кто его читает.
    """

    delta = set(changed)
    dirty = {tool.name for tool in REGISTRY.values() if _hits_source(tool) and tool.watched & delta}
    if not dirty:
        return frozenset()

    while True:
        produced: set[str] = set()
        for name in dirty:
            produced |= REGISTRY[name].provides
        next_dirty = set(dirty)
        for tool in REGISTRY.values():
            if not _hits_source(tool) or tool.name in dirty:
                continue
            if tool.watched & produced:
                next_dirty.add(tool.name)
        if next_dirty == dirty:
            return frozenset(dirty)
        dirty = next_dirty


def affected(changed: Iterable[str]) -> frozenset[str]:
    """Полное множество пересборки: грязные источники плюс всегдашние downstream."""

    return search_tools(changed) | DOWNSTREAM


def entry_step(changed: Iterable[str]) -> EscalationStep:
    """С какой ступени начинать ход. Дальше поднимает repair-цикл (T-08), не здесь."""

    delta = set(changed)
    if not delta:
        return EscalationStep.RE_RANK
    if delta <= RERANK_ONLY:
        return EscalationStep.RE_RANK
    # Состав группы может присутствовать во входе источника (для следующего поиска), но
    # для уже найденного допустимого множества сначала достаточно пересобрать размещение.
    # Комбинация «на троих» + явный total-budget также остаётся ступенью 1.
    if delta <= RERANK_ONLY | RECOMPOSE_ONLY:
        return EscalationStep.RECOMPOSE
    if not search_tools(delta):
        return EscalationStep.RECOMPOSE
    return EscalationStep.RE_SEARCH


def tools_for(changed: Iterable[str], step: EscalationStep) -> frozenset[str]:
    """Какие инструменты переоткрыть на этой ступени.

    Ступени 0 и 1 не ходят в источник вовсе — в таблице CONTRACTS.md у них «вызовов 0»
    и «0–1»; переиспользуется то, что уже лежит в `state.candidates`.
    """

    if step == EscalationStep.RE_SEARCH:
        return search_tools(changed)
    return frozenset()
