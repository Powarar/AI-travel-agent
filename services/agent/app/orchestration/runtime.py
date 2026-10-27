"""Рантайм оркестратора: цикл одного хода (`docs/ARCHITECTURE.md` §3).

Здесь живёт всё, что узлам запрещено: обращения к инструментам, счётчики хода, `interrupt`
с сериализацией состояния и возобновление по ответу. Узлы остаются чистыми, поэтому прогон
воспроизводим: тот же вход — та же последовательность фаз и те же числа.

Три правила, которые рантайм обязан соблюдать:

* `tools_for(changed, step)` — единственный источник списка инструментов: на ступени 0
  он пустой, значит переспрашивать источник нельзя («бюджет −30%» пересобирается из того,
  что уже лежит в памяти хода);
* счётчики ведёт рантайм: `llm_calls` на каждый слот, `tool_calls` на каждое обращение к
  инструменту (кэш-хит — обращение логики, но не источника), `questions_asked` на каждый
  заданный вопрос (не на полученный ответ), `wall_clock_ms` только из `time.monotonic()`;
* лимит хода не бросает исключение: исход `best_effort` со списком неисследованного.

`coherence_check` из реестра здесь не вызывается: он с `cost="none"`, а его пересчёт — это
и есть `validate()`, который и так downstream (`orchestration/graph.py`). Лишний вызов
только поднял бы `tool_calls` на том же самом коде.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from services.agent.app.llm.slots import Explanation, LLMSlots, MockSlots, display
from services.agent.app.orchestration import nodes, policy
from services.agent.app.orchestration.graph import entry_step, tools_for
from services.agent.app.orchestration.nodes import Composition, SearchResult
from services.agent.app.orchestration.policy import QuestionSpec
from services.agent.app.tools.registry import REGISTRY, ToolCache
from services.agent.app.validation.coherence import has_blocking, validate
from services.agent.app.validation.inputs import Plan
from shared.contracts.enums import EscalationStep, Outcome, RunStatus, Stage
from shared.contracts.money import BudgetLedger
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.state import Candidate, RunBudget, TripState

logger = logging.getLogger(__name__)

#: Заглушка источника (T-10 заменит её retrieval-ом поверх БД): аргументы → кандидаты.
Source = Callable[[Mapping[str, object]], SearchResult]

INSPIRE_TOOLS: frozenset[str] = frozenset({"suggest_destinations", "weather_climate"})
INSPIRE_TOOLS_ORDER: tuple[str, ...] = ("suggest_destinations", "weather_climate")
#: Порядок обхода источников в SEARCH: зависимые идут после тех, от которых они зависят.
COLLECT_ORDER: tuple[str, ...] = ("suggest_destinations", "weather_climate",
                                  "transport_search", "hotel_search", "transfer_search")
#: Инструмент SEARCH и срез `candidates`, куда ложится его ответ.
SEARCH_OUTPUT: dict[str, str] = {
    "transport_search": "transport",
    "hotel_search": "hotels",
    "transfer_search": "transfers",
}
#: Производные ключи аргументов: их считает код, в `TripRequest` их нет (registry.py).
DERIVED_ARGS: frozenset[str] = frozenset({"destinations", "transport.arrival_time", "dates.month", "basket"})
_NUMBER = re.compile(r"\d[\d \u00a0]*\d|\d")


@dataclass
class Scratch:
    """Допустимое множество хода в памяти процесса: ответы источников и собранный план.

    Решения — в `TripState`; здесь только то, что уже отдали инструменты, чтобы ступень 0
    пересобрала план без обращений к источнику (CONTRACTS.md, «Ступени эскалации»).

    Ограничение этого блока: после рестарта процесса черновика нет, и ступень 0/1 собрать
    план не из чего — сборка отдаст `CANDIDATES_EMPTY`, а не молча сходит в источник
    поверх `tools_for`. Снимает это T-10: кандидаты начинают читаться из БД, и `results`
    перестает быть единственным носителем данных между ходами.
    """

    results: dict[str, SearchResult] = field(default_factory=dict)
    composition: Composition | None = None


@dataclass
class Run:
    """Один ход: `run_id`, состояние, счётчики и то, что рантайм держит между узлами."""

    run_id: str
    state: TripState
    scratch: Scratch
    questions: dict[str, QuestionSpec] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    answered: set[tuple[str, str]] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    dirty: set[str] = field(default_factory=set)
    fetched: set[str] = field(default_factory=set)
    unresolved: list[str] = field(default_factory=list)
    explanation: str | None = None
    order_id: str | None = None
    trace: list[str] = field(default_factory=list)
    started_at: float = 0.0


class InterruptedRun(BaseModel):
    """То, что HTTP-слой кладёт в строку `run(status=interrupted)`: ход переживает рестарт.

    `results` и `composition` — черновик sources/сборки, не решение: решения все в `state`,
    поэтому в БД их можно не писать и возобновиться пересборкой. Но без них ход, прерванный
    на вопросе про hard-нарушение, терял бы уже собранный план.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: TripState
    questions: dict[str, QuestionSpec] = Field(default_factory=dict)
    texts: dict[str, str] = Field(default_factory=dict)
    answered: list[tuple[str, str]] = Field(default_factory=list)
    results: dict[str, SearchResult] = Field(default_factory=dict)
    composition: Composition | None = None
    trace: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class Runtime:
    """Оркестратор: рёбра из `policy`, чистые узлы из `nodes`, инструменты из реестра."""

    def __init__(self, sources: Mapping[str, Source], *, slots: LLMSlots | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sources = dict(sources)
        self._slots: LLMSlots = slots if slots is not None else MockSlots()
        self._clock = clock
        self._cache = ToolCache()
        self._scratch: dict[UUID, Scratch] = {}

    @property
    def cache(self) -> ToolCache:
        return self._cache

    # --------------------------------------------------------------- вход хода

    def start(self, message: str, *, state: TripState | None = None, trip_id: UUID | None = None,
              now: datetime | None = None) -> Run:
        """Новый ход: `POST /v1/trips/{id}/messages` → `run_id`.

        Лимиты считаются на ход, поэтому счётчики обнуляются; накопленное по путешествию
        (`questions_asked`) остаётся в состоянии.
        """

        base = state if state is not None else TripState(
            trip_id=trip_id if trip_id is not None else uuid4()
        )
        base = base.model_copy(update={"budgets": RunBudget()}, deep=True)
        run = Run(
            run_id=str(uuid4()),
            state=base,
            scratch=self._scratch.setdefault(base.trip_id, Scratch()),
            started_at=self._clock(),
        )
        before = run.state.request
        delta = self._slot_parse(run, message)
        if delta is None:
            return self._stop_by_limit(run)
        run.state = nodes.apply_delta(run.state, delta)
        self._route(run, before)
        logger.info("run started trip=%s run=%s changed=%d tools=%s", base.trip_id, run.run_id,
                    len(run.changed), sorted(run.dirty))
        return self._drive(run, _stamp(now))

    def answer(self, run: Run, path: str, raw: str, *, now: datetime | None = None) -> Run:
        """`POST /v1/runs/{run_id}/answers` — тот же ход, идемпотентно по (run_id, field).

        Вопрос засчитывается там, где рантайм его задаёт (`_ask`), а не здесь: повторный
        ответ по той же паре `(run_id, field)` не должен поднимать счётчик ещё раз.
        """

        key = (run.run_id, path)
        if key in run.answered:
            logger.info("duplicate answer ignored run=%s field=%s", run.run_id, path)
            return run
        if path not in run.state.awaiting_fields:
            raise ValueError(f"no pending question for {path} in run {run.run_id}")

        before = run.state.request
        run.state = nodes.apply_answer(run.state, path, raw)
        run.answered.add(key)
        run.questions.pop(path, None)
        run.texts.pop(path, None)
        # лимит 20 с — про вычисления контура, а не про то, когда человек успел ответить:
        # время хода продолжается с момента ответа
        run.started_at = self._clock()
        self._route(run, before)
        return self._drive(run, _stamp(now))

    def checkout(self, run: Run, *, idempotency_key: str | None = None,
                 now: datetime | None = None) -> Run:
        """Подтверждение пользователя после PLAN: `POST /v1/trips/{id}/checkout`.

        Ключ идемпотентности входит в аргументы `checkout_mock` (его `inputs` — `basket`),
        поэтому повтор с тем же ключом — тот же content-hash и тот же заказ.
        """

        if run.state.outcome != Outcome.PLAN_READY:
            raise ValueError(f"run {run.run_id} has no plan to confirm (outcome={run.state.outcome})")
        if run.order_id is not None:
            logger.info("checkout replay run=%s order=%s", run.run_id, run.order_id)
            return run
        args: dict[str, object] = {
            "basket": dict(sorted(run.state.chosen.component_refs.items())),
            "idempotency_key": idempotency_key if idempotency_key is not None else run.run_id,
        }
        result = self._call(run, "checkout_mock", args=args, now=_stamp(now))
        if result is None:
            return self._stop_by_limit(run)
        run.order_id = result.order_id
        run.state = nodes.checked_out(run.state, result.order_id)
        return self._close(run)

    # ----------------------------------------------------------- прерванный ход

    def dump(self, run: Run) -> str:
        """Сериализация прерванного хода — строку пишет П2 (T-06) в `run(status=interrupted)`."""

        payload = InterruptedRun(
            run_id=run.run_id,
            state=run.state,
            questions=dict(run.questions),
            texts=dict(run.texts),
            answered=sorted(run.answered),
            results=dict(run.scratch.results),
            composition=run.scratch.composition,
            trace=list(run.trace),
            unresolved=list(run.unresolved),
        )
        return payload.model_dump_json()

    def load(self, payload: str) -> Run:
        """Возобновление из строки БД: память процесса не участвует."""

        stored = InterruptedRun.model_validate_json(payload)
        scratch = self._scratch.setdefault(stored.state.trip_id, Scratch())
        for name, result in stored.results.items():
            scratch.results.setdefault(name, result)
        if scratch.composition is None:
            scratch.composition = stored.composition
        run = Run(
            run_id=stored.run_id,
            state=stored.state,
            scratch=scratch,
            questions=dict(stored.questions),
            texts=dict(stored.texts),
            answered=set(stored.answered),
            trace=list(stored.trace),
            unresolved=list(stored.unresolved),
            started_at=self._clock(),
        )
        logger.info("interrupted run loaded trip=%s run=%s awaiting=%s", stored.state.trip_id,
                    run.run_id, run.state.awaiting_fields)
        return run

    # --------------------------------------------------------------- цикл хода

    def _drive(self, run: Run, now: datetime) -> Run:
        while run.state.run_status == RunStatus.RUNNING:
            stage = policy.next_stage(run.state)
            if stage is None:
                logger.warning("no outgoing edge trip=%s stage=%s", run.state.trip_id,
                               run.state.stage.value)
                return self._best_effort(run, "no_outgoing_edge")
            run.trace.append(stage.value)
            run = self._handle(run, stage, now)
            if run.state.run_status == RunStatus.RUNNING:
                self._charge_clock(run)
        return run

    def _handle(self, run: Run, stage: Stage, now: datetime) -> Run:
        if stage == Stage.DECIDE:
            return self._decide(run)
        if stage == Stage.INSPIRE:
            return self._inspire(run, now)
        if stage == Stage.SEARCH:
            return self._search(run, now)
        if stage == Stage.COMPOSE:
            return self._compose(run, now)
        if stage == Stage.VALIDATE:
            return self._validate(run)
        if stage == Stage.SHORTLIST:
            return self._shortlist(run)
        if stage == Stage.EXPLAIN:
            return self._explain(run)
        if stage == Stage.PLAN:
            return self._plan(run)
        logger.warning("stage has no handler trip=%s stage=%s", run.state.trip_id, stage.value)
        return self._best_effort(run, f"no_handler_{stage.value}")

    def _decide(self, run: Run) -> Run:
        """DECIDE: спросить или идти собирать; фаза обязана отметить себя в `stage`.

        Ребро `policy.next_stage` читает завершённую фазу из состояния, поэтому узел без
        смены `stage` зациклил бы ход на том же самом DECIDE. Отметить `INSPIRE` здесь
        нельзя — это значило бы «INSPIRE уже отработала», и подбор направлений был бы
        пропущен; грязные inspire-инструменты добирает `SEARCH` (см. `_collect`).
        """

        specs = policy.questions_to_ask(run.state)
        if specs:
            return self._ask(run, specs[0])
        return self._mark(run, Stage.DECIDE)

    def _mark(self, run: Run, stage: Stage) -> Run:
        run.state = nodes.set_stage(run.state, stage)
        return run

    def _ask(self, run: Run, spec: QuestionSpec) -> Run:
        """Вопрос = interrupt: текст даёт слот №2, паузу и след в состоянии создаёт узел.

        Счётчик вопросов поднимает рантайм на самом вопросе: и в счётчик хода (`budgets`,
        лимит из контракта), и в накопленный по путешествию (`nodes.ask`).
        """

        text = self._slot_formulate(run, spec)
        if text is None:
            return self._stop_by_limit(run)
        self._charge(run, questions=1)
        run.questions[spec.field] = spec
        run.texts[spec.field] = text
        run.state = nodes.ask(run.state, spec)
        logger.info("question raised run=%s field=%s", run.run_id, spec.field)
        return run

    def _inspire(self, run: Run, now: datetime) -> Run:
        """INSPIRE: направления и климат (вход, когда своих направлений ещё нет)."""

        collected = self._collect(run, INSPIRE_TOOLS_ORDER, now)
        if collected is None:
            return self._stop_by_limit(run)
        return self._mark(collected, Stage.INSPIRE)

    def _search(self, run: Run, now: datetime) -> Run:
        """SEARCH: все источники хода в порядке зависимостей; решает `_needs`.

        Список полный намеренно: на дельте по датам выезда грязнеют и подбор направлений,
        и климат, а фаза INSPIRE в этот ход может не входиться.
        """

        collected = self._collect(run, COLLECT_ORDER, now)
        if collected is None:
            return self._stop_by_limit(run)
        return self._mark(collected, Stage.SEARCH)

    def _collect(self, run: Run, names: tuple[str, ...], now: datetime) -> Run | None:
        """Вызвать инструменты, которые надо переоткрыть, и унести их ответ в `candidates`.

        Финалистов слот №4 выбирает сразу после подбора направлений и до того, как по ним
        пойдут перелёты, отели и трансферы: иначе повторный подбор на дельте отдал бы
        источникам все двенадцать кодов вместо трёх.
        """

        for name in names:
            if not self._needs(run, name):
                continue
            result = self._call(run, name, args=self._args_for(run.state, name), now=now)
            if result is None:
                return None
            run.scratch.results[name] = result
            run.fetched.add(name)
            run.state = nodes.absorb(run.state, name, result)
            if name == "suggest_destinations":
                run = self._finalists(run)
        return run

    def _finalists(self, run: Run) -> Run:
        """Слот №4: из отобранного кодом набора — три направления с причиной."""

        if not run.state.candidates.destinations or not self._needs_finalists(run):
            return run
        finalists = self._slot_finalists(run, run.state.candidates.destinations)
        if finalists is None:
            return self._stop_by_limit(run)
        run.state = nodes.mark_finalists(run.state, finalists)
        return run

    def _compose(self, run: Run, now: datetime) -> Run:
        composition = nodes.compose(run.state, nodes.merge_results(list(run.scratch.results.values())),
                                    now=now)
        run.scratch.composition = composition
        if composition.plan is None:
            logger.info("composition failed trip=%s codes=%s", run.state.trip_id,
                        [violation.code.value for violation in composition.blocking])
            run.state = nodes.record_violations(run.state, composition.blocking)
            return self._resolve_blocking(run)
        run.state = nodes.commit_plan(run.state, composition)
        return run

    def _validate(self, run: Run) -> Run:
        if run.scratch.composition is None or run.scratch.composition.plan is None:
            raise ValueError("VALIDATE requires a composed plan")
        if run.state.request.budget.amount.is_unknown:
            # сравнивать сумму не с чем: бюджет, которого пользователь не называл, проверкой
            # не является — молча подставлять его под итог нельзя (инвариант №3)
            run.unresolved.append("budget.amount:unknown")
            return self._best_effort(run, "budget_unknown")
        violations = validate(run.scratch.composition.plan, self._ledger(run), run.state)
        run.state = nodes.record_violations(run.state, violations)
        if has_blocking(violations):
            return self._resolve_blocking(run)
        return run

    def _shortlist(self, run: Run) -> Run:
        """12→3 (слот №4) сыграно в INSPIRE на направлениях; здесь фаза отмечена, и счёт
        версий растёт честно: `COMPARE`/`DIFF` — T-08."""

        run.state = nodes.set_stage(run.state, Stage.SHORTLIST)
        return run

    def _explain(self, run: Run) -> Run:
        composition = run.scratch.composition
        if composition is None or composition.plan is None:
            raise ValueError("EXPLAIN requires a composed plan")
        plan = composition.plan
        ledger = self._ledger(run)
        check_in, check_out = self._stay_dates(run, plan)
        explanation = Explanation.from_parts(
            headline=composition.facts.get("headline", ""),
            nights=plan.nights,
            guests=plan.guests,
            check_in=check_in,
            check_out=check_out,
            ledger=ledger,
            facts={key: value for key, value in composition.facts.items() if key != "headline"},
        )
        text = self._slot_explain(run, explanation)
        if text is None:
            return self._stop_by_limit(run)
        bad = unverified_numbers(text, run.state, ledger)
        if bad:
            # guard из docs/LLM-BOUNDARY.md: одна перегенерация, потом шаблон из состояния
            logger.warning("number guard rejected explanation run=%s numbers=%s", run.run_id, bad)
            again = self._slot_explain(run, explanation)
            if again is None or unverified_numbers(again, run.state, ledger):
                text = self._template(explanation)
                logger.info("fell back to template run=%s", run.run_id)
            else:
                text = again
        run.explanation = text
        run.state = nodes.set_stage(run.state, Stage.EXPLAIN)
        return run

    def _stay_dates(self, run: Run, plan: Plan) -> tuple[date, date]:
        """Заезд и выезд для карточки: из плана, а если ночёвок в плане нет — из состояния.

        Случай не выдуманный: ответ на второй вопрос ушёл не в то поле, даты остались
        неизвестны, `compose` собрал план без ночёвок — и `plan.stays[0]` упал IndexError
        внутри живого хода. Падение посреди демо хуже явной ошибки с причиной.
        """

        if plan.stays:
            return plan.stays[0].date, plan.stays[-1].date + timedelta(days=1)
        start = run.state.request.dates.start.value
        if start is None:
            raise ValueError("EXPLAIN requires lodging or dates: neither is in the plan")
        return start, start + timedelta(days=max(plan.nights, 1))

    def _plan(self, run: Run) -> Run:
        run.state = nodes.finish(run.state, Outcome.PLAN_READY, wall_clock_ms=self._elapsed_ms(run))
        logger.info("plan ready trip=%s version=%s total=%s", run.state.trip_id,
                    run.state.version, self._ledger(run).total.as_display())
        return run

    # ------------------------------------------------------- исход и лимиты

    def _resolve_blocking(self, run: Run) -> Run:
        """Hard-нарушение не ослабляется молча: вопрос с посчитанными числами или `infeasible`."""

        spec = policy.question_for_blocking(run.state)
        if spec is not None:
            return self._ask(run, spec)
        run.unresolved = policy.blocking_actions([
            violation for violation in run.state.violations if violation.is_blocking
        ])
        outcome = policy.terminal_outcome(run.state.violations)
        logger.info("blocking left trip=%s outcome=%s unresolved=%s", run.state.trip_id,
                    outcome.value, run.unresolved)
        run.state = nodes.finish(run.state, outcome, wall_clock_ms=self._elapsed_ms(run))
        return run

    def _stop_by_limit(self, run: Run) -> Run:
        """Обрыв по исчерпанному лимиту: причину называет счётчик, а не тот, кто её заметил.

        Слот и инструмент возвращают `None` по одной и той же причине — `exhausted()`, — и
        подпись «tool_calls» при севшем `wall_clock` была бы неправдой: `unresolved` читают
        и пользователь, и метрики T-09 как объяснение, почему план не дошёл.
        """

        return self._best_effort(run, self._stop_reason(run) or "limit")

    def _best_effort(self, run: Run, limit: str) -> Run:
        """Лимит хода — не исключение: отдаём лучшее из найденного и список неисследованного."""

        logger.info("run stopped by limit run=%s limit=%s", run.run_id, limit)
        run.unresolved.append(f"limit:{limit}")
        run.state = nodes.finish(run.state, Outcome.BEST_EFFORT, wall_clock_ms=self._elapsed_ms(run))
        return run

    def _close(self, run: Run) -> Run:
        """Терминал вне фазового цикла (checkout): версия и время хода обновлены."""

        run.state = nodes.finish(run.state, Outcome.PLAN_READY, wall_clock_ms=self._elapsed_ms(run))
        return run

    # ------------------------------------------------------------ инструменты

    def _needs(self, run: Run, name: str) -> bool:
        """Открывать ли инструмент: он грязный по `tools_for` или его данных ещё нет.

        `run.fetched` — источники, уже отработавшие в этом ходу: INSPIRE и SEARCH обходят
        один и тот же список, и без этой проверки подбор направлений вызывался бы дважды
        за ход (кэш вернул бы тот же ответ, но `tool_calls` вырос бы на пустом месте).
        """

        if name in run.fetched:
            return False
        if name in run.dirty:
            return True
        bucket = SEARCH_OUTPUT.get(name)
        if bucket is not None:
            return not getattr(run.state.candidates, bucket)
        if name == "suggest_destinations":
            return not run.state.candidates.destinations
        if name == "weather_climate":
            return self._climate_missing(run.state)
        return False

    @staticmethod
    def _climate_missing(state: TripState) -> bool:
        """Климат спрашиваем только для финалистов — их показывает пользователю.

        Проверка по всем двенадцати кандидатам никогда не стала бы удовлетворена (`absorb`
        дописывает температуру лишь трём выбранным), и каждый следующий ход открывал бы
        `weather_climate` заново: кэш вернул бы тот же ответ, но `tool_calls` вырос бы на
        пустом месте — а лимит хода именно для таких обращений и придуман.
        """

        picked = [candidate for candidate in state.candidates.destinations
                  if candidate.ref.get("finalist") == "true"]
        if not picked:
            return True
        return not all(candidate.ref.get("day_temp_c") for candidate in picked)

    def _needs_finalists(self, run: Run) -> bool:
        return not any(candidate.ref.get("finalist") == "true"
                       for candidate in run.state.candidates.destinations)

    def _args_for(self, state: TripState, name: str) -> dict[str, object]:
        """Аргументы запроса к источнику — из объявленных `Tool.inputs`, а не на глаз."""

        args: dict[str, object] = {}
        for path in sorted(REGISTRY[name].inputs):
            if path in DERIVED_ARGS:
                args[path] = self._derived(state, path)
            else:
                args[path] = _plain(state.request.field_value(path))
        return args

    def _derived(self, state: TripState, path: str) -> object:
        if path == "destinations":
            return nodes.destination_codes(state)
        if path == "transport.arrival_time":
            return nodes.arrival_time(state)
        if path == "dates.month":
            return nodes.departure_month(state.request)
        if path == "basket":
            return dict(sorted(state.chosen.component_refs.items()))
        raise ValueError(f"no derived argument for {path}")

    def _call(self, run: Run, name: str, *, args: Mapping[str, object],
              now: datetime) -> SearchResult | None:
        if self._stop_reason(run):
            return None
        tool = REGISTRY[name]
        if not tool.ready(run.state):
            logger.info("tool skipped by guard trip=%s tool=%s", run.state.trip_id, name)
            return SearchResult()
        source = self._sources.get(name)
        if source is None:
            raise ValueError(f"no source wired for tool {name}")
        # обращение к инструменту — это ход; в источник идём только когда кэш пуст
        self._charge(run, tool=1)
        value, from_cache = self._cache.call(tool, args, lambda: source(args), now_s=now.timestamp())
        logger.info("tool called trip=%s tool=%s cached=%s source_calls=%d cache_hits=%d",
                    run.state.trip_id, name, from_cache, self._cache.source_calls, self._cache.cache_hits)
        if not isinstance(value, SearchResult):
            raise TypeError(f"tool {name} must return SearchResult, got {type(value).__name__}")
        return value

    def _ledger(self, run: Run) -> BudgetLedger:
        """Сумма плана — из `BudgetLedger`: `total` считается из линий, а не хранится."""

        budget = run.state.request.budget.amount.value
        scope = run.state.request.budget.scope.value
        if budget is None or scope is None or not run.state.chosen.items:
            raise ValueError("ledger needs a stated budget and committed plan lines")
        return BudgetLedger(lines=list(run.state.chosen.items), budget=budget, scope=scope)

    def _template(self, explanation: Explanation) -> str:
        return (f"{explanation.headline}: {explanation.nights} ночей, "
                f"{display(explanation.total)} из {display(explanation.budget)}.")

    # ------------------------------------------------------------------- слоты

    def _slot_parse(self, run: Run, message: str) -> TripRequest | None:
        if self._stop_reason(run):
            return None
        self._charge(run, llm=1)
        return self._slots.parse(message)

    def _slot_formulate(self, run: Run, spec: QuestionSpec) -> str | None:
        if self._stop_reason(run):
            return None
        self._charge(run, llm=1)
        return self._slots.formulate(spec)

    def _slot_finalists(self, run: Run, candidates: list[Candidate]) -> list[Candidate] | None:
        if self._stop_reason(run):
            return None
        self._charge(run, llm=1)
        return self._slots.pick_finalists(candidates)

    def _slot_explain(self, run: Run, explanation: Explanation) -> str | None:
        if self._stop_reason(run):
            return None
        self._charge(run, llm=1)
        return self._slots.explain(explanation)

    # ---------------------------------------------------------------- счётчики

    def _charge(self, run: Run, *, llm: int = 0, tool: int = 0, questions: int = 0) -> None:
        """Счётчики хода: копия состояния без смены версии — версию поднимает узел."""

        budgets = run.state.budgets.model_copy(update={
            "llm_calls": run.state.budgets.llm_calls + llm,
            "tool_calls": run.state.budgets.tool_calls + tool,
            "questions_asked": run.state.budgets.questions_asked + questions,
        })
        run.state = run.state.model_copy(update={"budgets": budgets})

    def _charge_clock(self, run: Run) -> None:
        run.state = run.state.model_copy(update={
            "budgets": run.state.budgets.model_copy(update={"wall_clock_ms": self._elapsed_ms(run)})
        })

    def _elapsed_ms(self, run: Run) -> int:
        return int((self._clock() - run.started_at) * 1000)

    def _stop_reason(self, run: Run) -> str | None:
        """Любой исчерпанный лимит хода, кроме вопросов: их нехватка — не остановка.

        Исчерпание лимита вопросов рантайм переживает молча: `policy.questions_to_ask`
        возвращает пустой список, поля остаются `unknown`/`defaulted` и видны в UI, а ход
        идёт дальше — так же ведёт себя `scripts/quiz_demo.py`.
        """

        hit = run.state.budgets.exhausted()
        return None if hit == "questions" else hit

    def _route(self, run: Run, before: TripRequest) -> None:
        """ROUTE: дельта → ступень → набор инструментов, который можно переоткрыть."""

        run.changed = nodes.changed_fields(before, run.state.request)
        run.fetched = set()
        step = entry_step(run.changed)
        run.dirty = set(tools_for(run.changed, step))
        if step == EscalationStep.RE_SEARCH:
            # ступень 2 = точечный re-search: у грязных инструментов кэш сбрасывается,
            # чистые остаются тёплыми (CONTRACTS.md, «Ступени эскалации дельты»)
            self._cache.invalidate(sorted(run.dirty))
        logger.info("routed trip=%s changed=%s step=%d tools=%s", run.state.trip_id,
                    sorted(run.changed), step.value, sorted(run.dirty))


def unverified_numbers(text: str, state: TripState, ledger: BudgetLedger) -> list[str]:
    """Числа ответа против состояния: ни одного, которого нет в `TripState`/`BudgetLedger`.

    Дешёвая проверка подстрокой — ровно тот механизм, который не даёт модели написать
    «152 000 ₽» вместо посчитанных 148 900 ₽ (`docs/LLM-BOUNDARY.md`, антигаллюцинационный
    guard). Источники чисел: состояние (даты, состав, `ref` кандидатов) и ledger — его
    `total`/`remaining`/`breakdown`, потому что их вычисляет код из тех же линий.
    """

    haystack = state.model_dump_json() + ledger.model_dump_json() + _ledger_numbers(ledger)
    found: list[str] = []
    for token in _NUMBER.findall(text):
        normalized = token.replace(" ", "").replace("\u00a0", "")
        if normalized not in haystack and normalized not in found:
            found.append(normalized)
    return found


def _ledger_numbers(ledger: BudgetLedger) -> str:
    amounts = [ledger.total, ledger.remaining, *ledger.breakdown.values()]
    return " ".join(f"{money.rounded().amount} {money.amount}" for money in amounts)


def _plain(value: object) -> object:
    """Значение поля в том виде, в каком его принимает `json.dumps` (content-hash аргументов)."""

    held = value.value if isinstance(value, FieldValue) else value
    if held is None:
        return ""
    if isinstance(held, Enum):
        return held.value
    if isinstance(held, (datetime, date)):
        return held.isoformat()
    if isinstance(held, (list, tuple, set, frozenset)):
        #: порядок влияет на content-hash: `{a, b}` и `{b, a}` обязаны дать один ключ, иначе
        #: кэш источников промахивается на равных наборах. Сортировка по строке — потому что
        #: элементы здесь разного типа (числа и строки), а `sorted` без ключа их не сравнит.
        return sorted((_plain(item) for item in held), key=str)
    if isinstance(held, (bool, int, float, str)):
        return held
    dump = getattr(held, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return str(held)


def _stamp(now: datetime | None) -> datetime:
    """Время хода решает рантайм и передаёт его узлам аргументом — узлы его не берут."""

    return now if now is not None else datetime.now(timezone.utc)
