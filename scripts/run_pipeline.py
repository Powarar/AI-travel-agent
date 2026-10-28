"""Полный прогон контура одной командой (приёмка T-07): сообщение → вопросы → план → mock-checkout.

Зачем отдельный скрипт, если есть `tests/scenarios/`: тест меряет инварианты на моках, а живую
модель из pytest видно только через `-s` и только одним взглядом. Здесь то же сообщение и те же
фикстуры источников, но собранные из окружения, поэтому один файл прогоняет контур и на моках
(бесплатно, офлайн), и на живом шлюзе — и печатает то, что потом уходит в README: вызовы по
слотам, время каждого слота, токены, wall-clock, решение guard'а.

Запуск:

    env -u LLM_API_KEY .venv/bin/python scripts/run_pipeline.py                  # моки, без сети
    env -u LLM_API_KEY .venv/bin/python scripts/run_pipeline.py --provider aitunnel
    env -u LLM_API_KEY .venv/bin/python scripts/run_pipeline.py --repeat 5
    env -u LLM_API_KEY .venv/bin/python scripts/run_pipeline.py --sources seed \\
        --message "хочу в сочи на неделю в ноябре, с 14 по 21, из Москвы, бюджет 150к"
    env -u LLM_API_KEY .venv/bin/python scripts/run_pipeline.py \\
        --answer origin=Москва --answer "dates.start=с 14 по 21"

Три границы, которые скрипт не переступает:

* источники — фикстуры сценария из `tests/scenarios/test_golden_path.py` (по умолчанию) или
  каталог `data/seeds` (`--sources seed`), но не БД: retrieval за П2 (T-10), и подменять его
  здесь значило бы мерить свой фильтр вместо чужого. Точка замены — `build_sources()`;
* слот №6 (`diff`) не вызывается: у него пока нет места в контуре, он появляется вместе с
  дельта-пересборкой в T-08. Пустая строка в таблице честнее фиктивного вызова;
* живые вызовы только когда `--provider` (или `LLM_PROVIDER`) не `mock`. Прогон по умолчанию
  бесплатный, и `env -u LLM_API_KEY` при старте обязателен: переменная shell'а из-под старой
  сессии перекрывает `.env`, и «офлайн» прогон начинает платить.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent.app.llm.provider_openai_compatible import load_env_file, slots_from_config
from services.agent.app.llm.rerank import MODEL_ENV as RERANK_MODEL_ENV
from services.agent.app.llm.rerank import Reranker
from services.agent.app.llm.slots import Explanation, LLMSlots, MockSlots, display
from services.agent.app.orchestration.runtime import Runtime, Source, unverified_numbers
from shared.contracts.enums import Outcome
from shared.contracts.money import BudgetLedger
from shared.contracts.state import TripState

#: Сообщение золотого пути — то же, на котором стоят ассерты T-05: менять его здесь значит
#: потерять сравнимость с тестом.
DEFAULT_MESSAGE = "хочу к тёплому морю в ноябре вдвоём с ребёнком, 150к, без долгих перелётов"
#: Дата тестов: источники фикстуруют цены относительно неё, и на реальной дате числа плана
#: разъедутся с ассертами. `--real-now` включает текущую дату, если нужно посмотреть «как сейчас».
FIXTURE_NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)

#: имя метода слота → (подпись в таблице, переменная окружения модели, номер слота на доске)
SLOTS: tuple[tuple[str, str, str], ...] = (
    ("parse", "SL-1 parse", "LLM_MODEL_PARSE"),
    ("formulate", "SL-2 question", "LLM_MODEL_QUESTIONS"),
    ("soft_relevance", "SL-3 relevance", RERANK_MODEL_ENV),
    ("pick_finalists", "SL-4 finalists", "LLM_MODEL_FINALISTS"),
    ("explain", "SL-5 explain", "LLM_MODEL_EXPLAIN"),
    ("diff", "SL-6 diff", "LLM_MODEL_DIFF"),
)


class MeasuredSlots:
    """Обёртка над настоящими слотами: считает вызовы и время по каждому слоту.

    Нужна именно обёрткой, а не правкой рантайма: `wall_clock_ms` хода уже ведёт рантайм, а тут
    нужен разрез «кто из шести тормозит», и это свойство прогона, а не свойства состояния.
    Возвраты тоже записываются: `None` у слота №3 означает «не ранжировали, остался порядок
    источника», и в отчёте это видно отдельно от «проранжировали».
    """

    def __init__(self, inner: LLMSlots) -> None:
        self._inner = inner
        self.ms: dict[str, list[float]] = {name: [] for name, _, _ in SLOTS}
        self.notes: dict[str, list[str]] = {name: [] for name, _, _ in SLOTS}

    @property
    def inner(self) -> LLMSlots:
        return self._inner

    #: провайдер отдаёт токены своим счётчиком, а рантайм читает его с обёртки: без проброса
    #: `budgets.tokens` снова стал бы нулём
    @property
    def tokens_used(self) -> int:
        return int(getattr(self._inner, "tokens_used", 0) or 0)

    def _measured(self, name: str, call: Callable[[], Any]) -> Any:
        started = time.monotonic()
        result = call()
        self.ms[name].append((time.monotonic() - started) * 1000)
        return result

    def parse(self, message: str) -> Any:
        return self._measured("parse", lambda: self._inner.parse(message))

    def formulate(self, spec: Any) -> str:
        return cast(str, self._measured("formulate", lambda: self._inner.formulate(spec)))

    def soft_relevance(self, query: str, documents: dict[str, str]) -> dict[str, float] | None:
        def call() -> dict[str, float] | None:
            scores = self._inner.soft_relevance(query, documents)
            self.notes["soft_relevance"].append("нет скоров — порядок источника"
                                                if scores is None else f"{len(scores)} отелей")
            return scores
        return cast("dict[str, float] | None", self._measured("soft_relevance", call))

    def pick_finalists(self, candidates: list[Any]) -> list[Any]:
        def call() -> list[Any]:
            picked = self._inner.pick_finalists(candidates)
            self.notes["pick_finalists"].append(f"{len(picked)} из {len(candidates)}")
            return picked
        return cast("list[Any]", self._measured("pick_finalists", call))

    def explain(self, explanation: Explanation) -> str:
        return cast(str, self._measured("explain", lambda: self._inner.explain(explanation)))

    def diff(self, old_plan: dict[str, str], new_plan: dict[str, str]) -> str:
        return cast(str, self._measured("diff", lambda: self._inner.diff(old_plan, new_plan)))


class ChatMockRerankLive:
    """Гибрид замеров SL-3: чат — моки T-05, слот №3 — живой `/rerank`.

    Приёмка слота №3 — качество порядка отелей и время одного обращения. Если вместе с
    реранкером включить чат, живыми становятся пять мест сразу: `parse` каждый раз отдаёт
    другие даты и бюджет, слот №4 зовёт других финалистов, текст объяснения другой — и
    разницу между прогонами нельзя приписать одной модели. С мок-чатом вход не меняется, и
    обращение к шлюзу остаётся ровно одно.

    Стоимость хода при этом настоящая: `tokens_used` читается с реранкера, и `MeasuredSlots`
    пробрасывает её в отчёт как обычный живой прогон.
    """

    def __init__(self, reranker: Reranker | None) -> None:
        self._reranker = reranker
        self._chat = MockSlots()

    @property
    def reranker(self) -> Reranker | None:
        return self._reranker

    @property
    def tokens_used(self) -> int:
        return 0 if self._reranker is None else int(self._reranker.tokens_used)

    def parse(self, message: str) -> Any:
        return self._chat.parse(message)

    def formulate(self, spec: Any) -> str:
        return self._chat.formulate(spec)

    def pick_finalists(self, candidates: list[Any]) -> list[Any]:
        return self._chat.pick_finalists(candidates)

    def explain(self, explanation: Explanation) -> str:
        return self._chat.explain(explanation)

    def diff(self, old_plan: dict[str, str], new_plan: dict[str, str]) -> str:
        return self._chat.diff(old_plan, new_plan)

    def soft_relevance(self, query: str,
                       documents: dict[str, str]) -> dict[str, float] | None:
        """Тот же путь, что у живого провайдера: ключ — `candidate_id`, `None` — «не чем ранжировать».

        Нормализацию скоров делает рантайм (`nodes.apply_relevance`), поэтому гибрид и живой
        прогон получают одинаковое приведение и сравнимы между собой.
        """

        if self._reranker is None or not documents or not query.strip():
            return None
        ids = list(documents)
        hits = self._reranker.rank(query, [documents[cid] for cid in ids])
        if not hits:
            return None
        return {ids[hit.index]: hit.score for hit in hits}

    def close(self) -> None:
        if self._reranker is not None:
            self._reranker.close()


def routing_summary(slots: LLMSlots) -> dict[str, str]:
    """Чем платит прогон, одной строкой: `chat=mock, rerank=<модель>`.

    Отдельная функция потому, что одно и то же число должно быть и в таблице консоли, и в
    `--json`: dry-run печатается именно здесь, и расходиться двум местам нечем.
    """

    if isinstance(slots, ChatMockRerankLive):
        return {"chat": "mock",
                "rerank": slots.reranker.model if slots.reranker else "выключен"}
    if isinstance(slots, MockSlots):
        return {"chat": "mock", "rerank": "выключен"}
    return {"chat": os.environ.get("LLM_MODEL", "") or "(LLM_MODEL не задан)",
            "rerank": os.environ.get(RERANK_MODEL_ENV, "").strip() or "выключен"}


def build_sources(kind: str = "fixture") -> dict[str, Source]:
    """Источники прогона: `fixture` — фикстуры сценария, `seed` — каталог `data/seeds`.

    Импорт из тестового модуля осознанный: фикстуры золотого пути — единственный набор, на
    котором ассерты T-05 сходятся до копейки, и остаются значением по умолчанию. `seed` —
    тот же интерфейс над реальными строками сида: он нужен ровно для проверки честности
    исходов («не нашли» должно означать «не нашли», а не «в фикстуре нет строчки»).
    """

    if kind == "seed":
        from services.agent.app.tools.seed_sources import seed_sources

        return seed_sources()
    from tests.scenarios.test_golden_path import SOURCES

    return cast("dict[str, Source]", dict(SOURCES))


@dataclass
class TurnReport:
    """Один прогон: счётчики хода, трасса и всё, что печатается в отчёт."""

    answers: list[tuple[str, str]] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    outcome: Outcome | None = None
    stage: str = ""
    tool_calls: int = 0
    llm_calls: int = 0
    questions: int = 0
    tokens: int = 0
    wall_clock_ms: int = 0
    source_calls: int = 0
    cache_hits: int = 0
    explanation_text: str = ""
    invented_numbers: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    finalists: list[str] = field(default_factory=list)
    relevance: list[tuple[str, str, str]] = field(default_factory=list)
    components: dict[str, str] = field(default_factory=dict)
    total: str = ""
    budget: str = ""
    remaining: str = ""
    over_budget: bool = False
    breakdown: list[tuple[str, str]] = field(default_factory=list)
    order_id: str | None = None
    failed: str | None = None
    state_dump: dict[str, Any] = field(default_factory=dict)


def walk(runtime: Runtime, message: str, answers: Mapping[str, str], *, now: datetime,
         ask: Callable[[str, str], str], report: TurnReport) -> None:
    """Сообщение → ответы на висящие вопросы → checkout; путь поля берётся из состояния.

    Тот же порядок, что в тесте: диктует `awaiting_fields[0]`, поэтому прогон не может ответить
    не на тот вопрос, если модель переставит вопросы местами. Ответ, который узел не смог
    прочесть, останавливает ход в `report.failed` — прогон остаётся прогоном с числами, а не
    трекбеком из `nodes._apply_dates`.
    """

    run = runtime.start(message, now=now)
    while run.state.awaiting_fields:
        path = run.state.awaiting_fields[0]
        raw = answers.get(path) or ask(path, run.texts.get(path, ""))
        report.answers.append((path, raw))
        try:
            run = runtime.answer(run, path, raw, now=now)
        except ValueError as error:
            #: ответ, который узел не смог прочитать («2026-11-12» вместо «с 12 по 17»), — это
            #: штатный провал прогона, а не трекбек: на живых слотах так отвечает и модель,
            #: и человек в терминале. Состояние остаётся предыдущим (`answer` не менял его),
            #: поэтому отчёт со счётчиками и числами хода всё равно отдаётся
            report.failed = f"ответ на вопрос {path!r} не прочитан: {error}"
            _fill(run, runtime, report)
            return
    if run.state.outcome != Outcome.PLAN_READY:
        report.failed = (f"до checkout не дошли: outcome={run.state.outcome} "
                         f"status={run.state.run_status.value}")
        _fill(run, runtime, report)
        return
    run = runtime.checkout(run, now=now)
    _fill(run, runtime, report)


def _fill(run: Any, runtime: Runtime, report: TurnReport) -> None:
    """Счётчики, план и числа хода в отчёт; ничего не пересчитывается, только читаем."""

    state: TripState = run.state
    budget = state.budgets
    #: состояние целиком — то, что ждёт фронт (T-06): план, кандидаты, provenance, счётчики.
    #: Сериализуется до `_fill`, а не в самом конце: прогон обязан отдавать JSON и когда
    #: денег в плане нет, и когда до плана не дошли.
    report.state_dump = json.loads(state.model_dump_json())
    report.trace = list(run.trace)
    report.outcome = state.outcome
    report.stage = state.stage.value
    report.tool_calls = budget.tool_calls
    report.llm_calls = budget.llm_calls
    report.questions = budget.questions_asked
    report.tokens = budget.tokens
    report.wall_clock_ms = budget.wall_clock_ms
    report.source_calls = runtime.cache.source_calls
    report.cache_hits = runtime.cache.cache_hits
    report.order_id = run.order_id
    report.explanation_text = run.explanation or ""
    report.violations = [f"{violation.code.value}:{violation.severity.value}"
                        for violation in state.violations]
    report.finalists = [candidate.candidate_id for candidate in state.candidates.destinations
                        if candidate.ref.get("finalist") == "true"]
    report.components = dict(state.chosen.component_refs)
    report.relevance = [(candidate.candidate_id, candidate.ref.get("relevance", "-"),
                         candidate.ref.get("name", ""))
                        for candidate in state.candidates.hotels]
    if not state.chosen.items:
        return
    amount = state.request.budget.amount.value
    scope = state.request.budget.scope.value
    if amount is None or scope is None:
        # без бюджета ledger не собирается: сумма — hard-поле, и его отсутствие в отчёте
        # должно быть видно как пустая строка, а не как исключение прогона
        return
    ledger = BudgetLedger(lines=list(state.chosen.items), budget=amount, scope=scope)
    report.total = display(ledger.total)
    report.budget = display(ledger.budget)
    report.remaining = display(ledger.remaining)
    report.over_budget = ledger.over_budget is not None
    report.breakdown = [(kind.value, display(amount)) for kind, amount in ledger.breakdown.items()]
    if run.explanation:
        report.invented_numbers = unverified_numbers(run.explanation, state, ledger)


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def slot_models(slots: LLMSlots) -> dict[str, str]:
    """Какая модель стоит на каждом слоте — из окружения, а не из памяти провайдера.

    Одно и то же число читает и таблица в консоли, и `--json`: два маршрута по окружению
    разошлись бы на первом же прогоне с `LLM_MODEL_PARSE`, и таблица вёрстки стала бы
    враньём ровно тогда, когда мы считаем цену.
    """

    if isinstance(slots, MockSlots):
        return {label: "mock (T-05)" for _, label, _ in SLOTS}
    if isinstance(slots, ChatMockRerankLive):
        #: чат остаётся моком, и врать в таблице про пять слотов, которые не вызывались, нельзя
        hybrid = {label: "mock (T-05)" for _, label, _ in SLOTS}
        hybrid["SL-3 relevance"] = (slots.reranker.model if slots.reranker is not None
                                     else "(слот выключен: RERANK_MODEL не задан)")
        return hybrid
    base = os.environ.get("LLM_MODEL", "")
    models: dict[str, str] = {}
    for name, label, env_name in SLOTS:
        chosen = os.environ.get(env_name, "").strip()
        if name == "diff":
            chosen = f"{chosen or base or '(не задан)'} — не вызывается до T-08"
        elif name == "soft_relevance":
            chosen = chosen or "(слот выключен: RERANK_MODEL не задан)"
        elif not chosen:
            chosen = base or "(LLM_MODEL не задан)"
        models[label] = chosen
    return models


def print_routing(slots: LLMSlots, sources: str = "fixture") -> None:
    """Какая модель стоит на каждом слоте и откуда берутся данные — до первого вызова,
    чтобы цену видно было до того, как она потрачена."""

    print(f"провайдер: {type(slots).__name__}")
    print(f"  источники: {sources}"
          + (" — фикстуры золотого пути, офлайн" if sources == "fixture"
             else " — каталог data/seeds"))
    if isinstance(slots, ChatMockRerankLive):
        summary = routing_summary(slots)
        print(f"  chat={summary['chat']} (бесплатно), rerank={summary['rerank']}")
        print("  на ход: 0 чат-вызовов, 1 /rerank на прогон, если слот №3 включён")
        return
    if isinstance(slots, MockSlots):
        print("  все слоты — моки T-05: сеть не трогается, денег нет")
        return
    models = slot_models(slots)
    for name, label, env_name in SLOTS:
        print(f"  {label:<16} {env_name:<20} {models[label]}")
    print("  на ход: 1 чат-вызов на parse, по одному на каждый вопрос (лимит 2), 1 на finalists, "
          "1 на explain + 1 /rerank, если слот №3 включён")


def print_turn(report: TurnReport, message: str, index: int) -> None:
    print(f"\n=== прогон {index}: {message}")
    for path, raw in report.answers:
        print(f"  ответ {path} ← {raw}")
    print(f"  трасса: {' → '.join(report.trace)}")
    print(f"  stage={report.stage} outcome={report.outcome} "
          f"llm_calls={report.llm_calls} tool_calls={report.tool_calls} "
          f"questions={report.questions} tokens={report.tokens} "
          f"wall_clock_ms={report.wall_clock_ms}")
    print(f"  обращений к источнику: {report.source_calls}, попаданий в кэш: {report.cache_hits}")
    print(f"  финалисты: {', '.join(report.finalists) or '(нет)'}")
    scored = [row for row in report.relevance if row[1] != "-"]
    if scored:
        print("  релевантность отелей (слот №3):")
        for candidate_id, score, name in sorted(scored, key=lambda row: row[1], reverse=True):
            print(f"    {score}  {candidate_id}  {name}")
    elif report.relevance:
        print("  релевантность не выставлялась — порядок источника (score + стоимость ночи)")
    print(f"  компоненты плана: {report.components}")
    if report.total:
        print(f"  деньги: {report.total} из {report.budget}, остаток {report.remaining}"
              + (" — БЮДЖЕТ ПРЕВЫШЕН" if report.over_budget else ""))
        for kind, amount in report.breakdown:
            print(f"    {kind}: {amount}")
    print(f"  нарушения: {', '.join(report.violations) or '(нет)'}")
    print(f"  выдуманные числа guard'а: {report.invented_numbers or '(нет)'}")
    print(f"  заказ: {report.order_id}")
    print(f"  объяснение: {report.explanation_text}")
    if report.failed is not None:
        print(f"  !! {report.failed}")


def print_slots(measured: MeasuredSlots) -> None:
    print("\n=== слоты за весь прогон")
    print(f"  {'слот':<16} {'вызовов':>7} {'п50,мс':>9} {'п95,мс':>9}  заметки")
    for name, label, _ in SLOTS:
        times = measured.ms[name]
        median = statistics.median(times) if times else 0.0
        print(f"  {label:<16} {len(times):>7} {median:>9.0f} {_percentile(times, 0.95):>9.0f}  "
              f"{'; '.join(measured.notes[name])}")
    print(f"  токенов насчитал провайдер: {measured.tokens_used}")
    failures = getattr(measured.inner, "failures", [])
    for item in failures:
        print(f"  отказ чата: slot={item.get('slot')} kind={item.get('kind')} "
              f"attempts={item.get('attempts')} errors={item.get('errors')}")
    reranker = getattr(measured.inner, "reranker", None)
    for item in getattr(reranker, "failures", []) if reranker is not None else []:
        print(f"  отказ /rerank: {item}")


def summarize(reports: Sequence[TurnReport]) -> None:
    done = [report for report in reports if report.failed is None]
    print("\n=== итог")
    print(f"  прогонов: {len(reports)}, дошли до плана: {len(done)}")
    if not done:
        return
    walls = [report.wall_clock_ms for report in done]
    print(f"  wall-clock хода: п50 {int(statistics.median(walls))} мс, макс {max(walls)} мс "
          f"(лимит 20 000)")
    print(f"  токенов: {sum(report.tokens for report in done)}")
    clean = [report for report in done if not report.invented_numbers]
    print(f"  guard: чист на {len(clean)} из {len(done)}")


def json_payload(reports: Sequence[TurnReport], measured: MeasuredSlots, *,
                 provider: str, sources: str, message: str, answers: Mapping[str, str],
                 slots: LLMSlots) -> dict[str, Any]:
    """Ответ прогона одним объектом: метрика, план, состояние и маршрут моделей.

    Фронту (T-06) и замеру слотов нужен один и тот же результат в машиночитаемом виде:
    человек читает таблицу, а `--repeat` по 12 фразам никто таблицей не разберёт. Поля
    названы как в состоянии, и `state` лежит целиком — пересобирать numbers из строк
    объяснения значило бы иметь два источника чисел (инвариант №1).

    Слот №3 без `RERANK_MODEL` отдаёт `None`, и в JSON это видно как `"relevance": null`,
    а не как пропущенный ключ: «не ранжировали» и «не замеряли» — разные исходы прогона.
    """

    done = [report for report in reports if report.failed is None]
    walls = [report.wall_clock_ms for report in done]
    runs: list[dict[str, Any]] = []
    for report in reports:
        runs.append({
            "answers": [dict(zip(("path", "value"), pair)) for pair in report.answers],
            "trace": report.trace,
            "outcome": report.outcome.value if report.outcome is not None else None,
            "stage": report.stage,
            "failed": report.failed,
            "order_id": report.order_id,
            "explanation": report.explanation_text,
            "violations": report.violations,
            "invented_numbers": report.invented_numbers,
            "finalists": report.finalists,
            "relevance": [{"candidate_id": cid, "relevance": None if score == "-" else score,
                           "name": name}
                          for cid, score, name in report.relevance],
            "components": report.components,
            "money": {"total": report.total, "budget": report.budget,
                      "remaining": report.remaining, "over_budget": report.over_budget,
                      "breakdown": [dict(zip(("kind", "amount"), row))
                                    for row in report.breakdown]},
            "metrics": {"tool_calls": report.tool_calls, "llm_calls": report.llm_calls,
                        "questions": report.questions, "tokens": report.tokens,
                        "wall_clock_ms": report.wall_clock_ms,
                        "source_calls": report.source_calls, "cache_hits": report.cache_hits},
            "state": report.state_dump,
        })
    return {
        "provider": provider,
        "sources": sources,
        "message": message,
        "answers": dict(answers),
        "models": slot_models(slots),
        "runs": runs,
        "slots": [
            {"slot": label, "env": env_name, "calls": len(measured.ms[name]),
             "p50_ms": round(_percentile(measured.ms[name], 0.5)),
             "p95_ms": round(_percentile(measured.ms[name], 0.95)),
             "notes": measured.notes[name]}
            for name, label, env_name in SLOTS
        ],
        "failures": list(getattr(measured.inner, "failures", [])),
        "rerank_failures": list(getattr(
            getattr(measured.inner, "reranker", None), "failures", []) or []),
        "summary": {
            "runs": len(reports),
            "reached_plan": len(done),
            "wall_clock_ms_p50": int(statistics.median(walls)) if walls else 0,
            "wall_clock_ms_max": max(walls) if walls else 0,
            "tokens": sum(report.tokens for report in done),
            "tokens_by_provider": measured.tokens_used,
            "guard_clean": sum(1 for report in done if not report.invented_numbers),
        },
    }


def build_slots(provider: str, rerank_only: bool = False) -> LLMSlots:
    """Слоты из флага или окружения: `mock` — моки T-05, иначе живой провайдер.

    Флаг не дублирует `LLM_PROVIDER` каприза ради: живой прогон не должен зависеть от того, что
    осталось в `.env` после замера другой модели, а `--provider mock` обязан гарантированно
    ничего не стоить.

    `--rerank-only` не смотрит на `provider`: чат остаётся моком, живым становится один слот №3.
    Реранкер включается только явной `RERANK_MODEL` — без имени слот считается выключенным,
    иначе прогон «посмотреть быстрее» платил бы дефолтной моделью молча.
    """

    if rerank_only:
        if not os.environ.get(RERANK_MODEL_ENV, "").strip():
            return ChatMockRerankLive(None)
        return ChatMockRerankLive(Reranker.from_config())
    if provider == "mock":
        return MockSlots()
    os.environ["LLM_PROVIDER"] = provider
    return cast(LLMSlots, slots_from_config())


def _answers_from_args(values: Sequence[str]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for value in values:
        path, _, raw = value.partition("=")
        if not raw.strip():
            raise SystemExit(f"--answer ждёт поле=значение, получил {value!r}")
        answers[path.strip()] = raw.strip()
    return answers


def _ask_terminal(path: str, question: str) -> str:
    print(f"\n? {question}")
    answer = input(f"  ответ для {path}: ").strip()
    if not answer:
        raise SystemExit(f"пустой ответ на {path} — прогон остановлен")
    return answer


def _ask_refuse(path: str, question: str) -> str:
    raise SystemExit(f"модель спросила {path} ({question!r}); ответ задайте флагом "
                     f"--answer {path}=<значение>: stdin не терминал, спрашивать не у кого")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Прогон контура целиком: моки или живой провайдер")
    parser.add_argument("--provider", default=os.environ.get("LLM_PROVIDER", "mock"),
                        help="mock (по умолчанию, без сети) или aitunnel")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--answer", action="append", default=[], metavar="ПОЛЕ=ЗНАЧЕНИЕ")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--real-now", action="store_true",
                        help="текущая дата вместо даты фикстур (числа плана поплывут)")
    parser.add_argument("--dry-run", action="store_true",
                        help="только маршрутинг моделей и план вызовов; ни одного запроса к шлюзу")
    parser.add_argument("--verbose", action="store_true",
                        help="INFO-логи рантайма и провайдера в stderr")
    parser.add_argument("--json", action="store_true",
                        help="ответ одним JSON в stdout вместо таблиц (для фронта и замеров)")
    parser.add_argument("--sources", choices=("fixture", "seed"), default="fixture",
                        help="откуда данные: фикстуры золотого пути (по умолчанию) "
                             "или каталог data/seeds")
    parser.add_argument("--rerank-only", action="store_true",
                        help="гибрид SL-3: чат-слоты остаются моками, живой только /rerank "
                             "(включается переменной RERANK_MODEL)")
    args = parser.parse_args(argv)

    load_env_file()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    #: гибрид по определению «чат — моки»: `build_slots` на флаг смотрит раньше провайдера, и
    #: живая модель чата с ним просто не собирается. Принять флаг и сделать по-своему значило
    #: бы заплатить за чат, ожидая бесплатного прогона
    if args.rerank_only and args.provider.strip().lower() != "mock":
        print("--rerank-only означает «чат — моки»: живой провайдер чата с ним исключается",
              file=sys.stderr)
        return 2

    answers = _answers_from_args(args.answer)
    #: в `--json` ответ берём только из флага: печатать вопрос в stdout значит испортить
    #: JSON, а молча подставить ответ — значит сфабриковать прогон
    ask = _ask_refuse if args.json or not sys.stdin.isatty() else _ask_terminal
    #: порядок важен: сборка источников ничего не стоит, а провайдер держит `httpx.Client`,
    #: который на раннем выходе некому закрыть — поэтому «сида нет» проверяется первым
    try:
        sources: Mapping[str, Source] = build_sources(args.sources)
    except FileNotFoundError as error:
        #: `--sources seed` без `data/seeds`: сообщением-инструкцией, а не трекбеком
        #: из первого инструмента посреди уже оплаченного прогона
        print(f"источник {args.sources!r} не собран: {error}", file=sys.stderr)
        return 2
    try:
        slots = MeasuredSlots(build_slots(args.provider, args.rerank_only))
    except ValueError as error:
        # конфигурация, а не падение контура: трекбэк из argparse-скрипта только засорит вывод
        print(f"провайдер {args.provider!r} не собран: {error}", file=sys.stderr)
        print("нужно в .env: LLM_PROVIDER=aitunnel, LLM_MODEL=<чат-модель>, LLM_API_KEY; "
              "слот №3 включается переменной RERANK_MODEL", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc) if args.real_now else FIXTURE_NOW
    if not args.json:
        print_routing(slots.inner, args.sources)
    if args.dry_run:
        # конструирование провайдера сети не трогает, а клиент надо закрыть: незакрытый
        # `httpx.Client` переживает процесс только в тестах
        close = getattr(slots.inner, "close", None)
        if callable(close):
            close()
        if args.json:
            #: `routing` — те же два слова, что печатает консоль: фронту по dry-run надо знать,
            #: платит ли этот прогон (rerank=выключен) или нет, и брать это из одного места
            print(json.dumps({"provider": args.provider, "sources": args.sources,
                              "dry_run": True, "routing": routing_summary(slots.inner),
                              "models": slot_models(slots.inner)},
                             ensure_ascii=False, indent=2))
        else:
            print("\n--dry-run: прогон не начат, запросов к шлюзу не было")
        return 0

    reports: list[TurnReport] = []
    try:
        for index in range(1, args.repeat + 1):
            # новый Runtime на прогон: кэш инструментов и черновик хода не должны переходить
            # из прогонов в прогоны, иначе второй «прогон» ничего не считает
            runtime = Runtime(sources, slots=slots)
            report = TurnReport()
            walk(runtime, args.message, answers, now=now, ask=ask, report=report)
            reports.append(report)
            if not args.json:
                print_turn(report, args.message, index)
    finally:
        close = getattr(slots.inner, "close", None)
        if callable(close):
            close()

    if args.json:
        # один объект на stdout: `--json | jq` и запись замеров в файл работают без
        # зачистки таблиц, а логи провайдера уже идут в stderr
        print(json.dumps(json_payload(reports, slots, provider=args.provider,
                                      sources=args.sources, message=args.message,
                                      answers=answers, slots=slots.inner),
                         ensure_ascii=False, indent=2))
    else:
        print_slots(slots)
        summarize(reports)
    bad = [report for report in reports
           if report.failed is not None or report.invented_numbers
           or report.outcome not in {Outcome.PLAN_READY, Outcome.BEST_EFFORT}]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
