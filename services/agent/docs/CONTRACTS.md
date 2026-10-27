# Контракты

Фиксируется до кода. Контракт с фронтом — предмет договорённости 25-го, а не 28-го.

## TripRequest

Каждое поле несёт метаданные, а не только значение:

```python
class FieldStatus(str, Enum):        # unknown — не то же самое, что выдуманное значение
    KNOWN = "known"                  # сказал пользователь
    INFERRED = "inferred"            # выведено из контекста, требует подтверждения
    DEFAULTED = "defaulted"          # подставлен безопасный дефолт
    UNKNOWN = "unknown"              # неизвестно, решаем спрашивать или нет

class ConstraintKind(str, Enum):
    HARD = "hard"                    # молча не ослабляется никогда
    SOFT = "soft"                    # превращается в штраф в ranking
```

| Поле | Тип | kind по умолчанию | Примечание |
|---|---|---|---|
| `origin` | `str \| None` (город/IATA) | hard | unknown → один вопрос, дефолт опасен |
| `direction.mode` | `explicit \| open \| forbidden` | hard | `open` = единственный триггер INSPIRE |
| `direction.explicit` | `str \| None` | hard | |
| `direction.excluded` | `list[str]` | hard | |
| `dates.start` / `dates.end` | `date \| None` | hard | |
| `dates.flexibility_days` | `int` (0–7) | soft | unknown → 0, не выдумывать |
| `nights` | `int \| None` | hard | выводится из дат, не парсится отдельно |
| `party.adults` | `int` | hard | unknown → 2 + `DEFAULTED` |
| `party.children` | `list[int]` возрастов | hard | влияет на rooms и трансфер |
| `party.pets` | `bool` | hard | фильтрует hotels/transport/transfer |
| `budget.amount` / `.currency` | `Decimal` / `str` | hard | RUB по умолчанию для RU-запроса, `DEFAULTED` |
| `budget.scope` | `total \| per_person` | hard | частый источник конфликтов, уточнять при расхождении >2× |
| `budget.includes` | `set[transport, lodging, activities, transfer]` | hard | |
| `transport.modes` | `set[plane, train, auto]` | soft | |
| `transport.max_hours_direct` | `float \| None` | hard | «не хочу тратить много времени на дорогу» |
| `transport.prefer_direct` | `bool` | soft | |
| `transport.overnight_ok` | `bool` | soft | первое поле для дешёвого repair |
| `lodging.rooms` | `int` | hard | выводится из party |
| `lodging.stars_min` | `int \| None` | soft | |
| `lodging.budget_per_night_max` | `Decimal \| None` | soft | выводится из budget |
| `lodging.must` | `set[str]` (удобства) | hard | `pet_friendly` при pets=true |
| `themes` | `set[beach, city, nature, culture, food]` | soft | |
| `soft_preferences` | `list[str]` дословные фразы | soft | идут в `pref_text` для эмбеддинга |
| `identity.citizenship` / `residence` | `str \| None` | hard | **только** для флага `visa_required_for`, не для рекомендаций |

Не извлечённое поле остаётся `UNKNOWN`. Модель не имеет права подставлять значение без
`source="default"` — это отдельный тип ошибки в eval-наборе (fabricated field).

## Политика hard / soft

Жёсткость — это **не про точность значения**, а про то, разрешено ли нарушить поле ради
более привлекательного результата. Берётся из формулировки пользователя, а не из имени поля.

| Как появилось значение | `kind` / `status` | Отступление |
|---|---|---|
| Сказано явно: «в Италию», «150 000 на всё», «не больше 4 часов в дороге» | `hard` | никогда молча; только через вопрос |
| Сказано размыто: «атмосферный», «не слишком много туристов» | `soft` | да, штраф в ranking |
| Выведено из контекста: «вдвоём» из «с женой» | `inferred` | только после подтверждения пользователем |
| Подставлен дефолт: валюта RUB, `adults=2`, `flexibility_days=0` | `defaulted` | да; видно в UI как «предположено» |
| Системное/легальное ограничение | `hard`, не торгуется | вариант исключается из допустимого множества, не штрафуется |

По умолчанию **hard** только у: `dates.start/end`, `party.*`, `budget.amount|scope|includes`,
`direction.explicit` (если названо), `origin`, легальные флаги, `lodging.must`. Всё
остальное — `soft`. Причина: если hard-полей много, агент вечен в уточнениях; если мало —
молча подменяет сказанное.

`direction.mode`:

- `explicit` («хочу в Италию») → альтернативная страна никогда не подставляется молча; она
  идёт отдельным блоком «почему не Италия» с посчитанной разницей и только по согласию.
- `open` («куда-нибудь к тёплому морю») → ограничения нет вообще, это триггер INSPIRE.
- `forbidden` («только не Египет») → hard-исключение, фильтром.

**Конфликт hard-ограничений — это вопрос, а не решение агента.** Агент не знает, какое из
требований пользователь считает главным, поэтому спрашивает с уже посчитанными
компромиссами, после первого замера, а не заранее:

```
«В Италию, 7 ночей, вдвоём: минимум 168 400 ₽ — на 18 400 выше бюджета.
 1) остаться в Италии → отель 3* или −2 ночи
 2) уложиться в 150 000 → Албания / Тунис, вот чем отличаются
 3) поднять бюджет»
```

Отсюда правило repair: сначала ступени 0/1, затем targeted re-search, и только когда
нарушение `HARD_CONSTRAINT_UNSATISFIED` не снимается без изменения требования — `ASK` с
конкретикой. Ослабить hard ради «результат выглядит лучше» запрещено (инвариант №2).

## Money и учёт расходов

Деньги не `int` и не `float`:

```python
Money(amount: Decimal, currency: str)
PriceLine(kind: transport|lodging|transfer|activity|fee, amount, scope: total|per_person|per_night,
          included_in_budget: bool, source_id, fetched_at, data_status)
BudgetLedger(lines: list[PriceLine], total: Money, budget: Money, scope: BudgetScope)
```

Правила:

- `Decimal`, копеечный масштаб, округление **один раз** при итоговом подсчёте,
  `ROUND_HALF_UP`; промежуточные суммы не округляем.
- Городской/курортный сбор, топливный сбор, сервисный сбор, страховка — **отдельные
  `PriceLine`**, а не «прибавлено к цене», иначе пользователь не поймёт разницу.
- Пересчёт валют — только по курсу с `source_id` и `fetched_at`; при отсутствии курса —
  `UNKNOWN_CURRENCY`, не оценка на глаз.
- `budget.includes` решает, что попадает в итог: если `transfer` не включён, он всё равно
  показывается, но вне суммы и помечен «не учтён в бюджете».
- Двойной учёт ловится на уровне строк (одна и та же `PriceLine` не может попасть в
  `total` дважды) — это отдельный код нарушения `DOUBLE_COUNTED`.

Итог `BudgetLedger` — единственное место, откуда берутся числа для объяснения; guard сверяет
текст именно с ним.

## Противоречия в требованиях

Сообщение за сообщением пользователь может сказать «вдвоём» → «нас трое» → «с нами ещё
друг». Порядок разрешения:

1. Явное утверждение нового значения побеждает (`source=user`, `status=known`).
2. Поле, затронутое дважды с конфликтом, не «последнее wins» молча: создаётся
   `CONFLICTING_STATE` (warning) и поле помечается `needs_confirmation`.
3. Если противоречие задевает hard-поле и меняет результат — вопрос, с цитатой обоих
   значений («ранее: вдвоём, сейчас: трое — считаю трёх?»).
4. `inferred`, с которым пользователь не соглашается, понижается и не возвращается сам.
5. Любое изменение hard-поля пишется в `audit_events` со ссылкой на сообщение.


## TripState

```python
class TripState(BaseModel):
    trip_id: UUID
    version: int                     # растёт на каждый ход, никогда не мутируется on-place
    request: TripRequest
    stage: Stage                     # PARSE|CLARIFY|INSPIRE|SEARCH|COMPOSE|VALIDATE|REPAIR|SHORTLIST|COMPARE|EXPLAIN|PLAN|CHECKOUT
    candidates: Candidates           # destinations[], transport[], hotels[], transfers[]
    chosen: Basket                   # то, что пользователь подтвердил — не перезаписывается молча
    violations: list[Violation]
    repair_log: list[RepairAttempt]
    questions_asked: int
    budgets: RunBudget               # tool_calls, llm_calls, wall_clock, tokens
    provenance: dict[str, DataStatus]
```

История чата — не состояние. Важные решения живут в `TripState`, сообщения только
породляют `request`-delta. Это даёт: пересборку без «забыл, что мы уже выбрали отель»,
replay прогона и изменение одного компонента без потери остальных.

## Реестр инструментов

```python
Tool(
  name="hotel_search",
  inputs={"destinations", "dates.start", "dates.end", "nights",
          "party.adults", "party.children", "party.pets",
          "lodging.rooms", "lodging.stars_min", "lodging.budget_per_night_max",
          "lodging.must", "soft_preferences", "budget.includes"},
  guard=lambda s: bool(s.candidates.destinations) or not s.request.direction.explicit.is_unknown,
  guard_fields={"direction.explicit", "destinations", "dates.start", "dates.end"},
  output="candidates.hotels",
  cost="high",
  retries=3, backoff=(0.5, 1.0, 2.0), jitter=True, timeout=3.0,
  breaker=(failures=3, window=60), fallback="stale_cache",
  cache_ttl=3600,                              # по content-hash аргументов
)
```

| Инструмент | inputs (ключевое) | cost | fallback |
|---|---|---|---|
| `suggest_destinations` | month, party, transport.max_hours_direct, budget | med | stale-таблица |
| `weather_climate` | country, month | low | cache 7d |
| `transport_search` | origin, destination, dates, party | high | breaker → stale |
| `hotel_search` | destination, dates, party, lodging, soft_preferences | high | breaker → stale |
| `transfer_search` | destination, arrival_time, party, pets | low | default-оценка + `UNKNOWN` |
| `coherence_check` | (локально, без сети) | none | — |
| `checkout_mock` | basket, idempotency_key | none | идемпотентный повтор |

Направление в реестре называется `destinations` (в состоянии — `candidates.destinations`),
одна и та же строка фигурирует и в `provides` подборщика, и в `inputs` поиска. `guard` —
обычная функция над типизированным состоянием, не выражение в DSL (ADR-004).

`affected(changed_fields)` = реверс-индекс полей + транзитивное замыкание вниз
(`compose` и `validate` — всегда downstream). Три правила замыкания:

- инструмент грязный, если дельта попала в `watched` = `inputs` ∪ `guard_fields`: поле либо
  уходит в запрос к источнику, либо меняет готовность запуска (`guard`);
- дальше замыкаем по `provides`: пересобранный `transport.arrival_time` тянет трансфер,
  пересобранные `destinations` — климат, перелёты и отели;
- инструменты с `cost="none"` (`coherence_check`, `checkout_mock`) в грязное множество не
  заносятся: к источнику они не ходят, а их локальный пересчёт — это и есть `validate`.

Общая `budget.amount` не входит в `inputs` поиска отелей: сумма — проверочное число
(`BudgetLedger`, T-03), а в запросе к источнику работает `lodging.budget_per_night_max`.
Иначе «бюджет −30%» на ступени 0 ложно объявлял бы отельный поиск грязным.

## Ступени эскалации дельты

| Ступень | Действие | Вызовов | Пример |
|---|---|---|---|
| 0 | пере-ранжировать уже найденных | 0 | «бюджет −30%» |
| 1 | пересчитать выбранный план на новых входных | 0–1 | «на троих» влезает ли в номер |
| 2 | re-search только грязных инструментов | 2–3 | «у нас собака» |
| 3 | расширить поиск (другие даты/направление) | 5+ | только с разрешения, если задевает hard |

Ступени 0 и 1 не обращаются к источнику вовсе: `tools_for(changed, step)` отдаёт пустое
множество, пересобираются только порядок, выбранный план и проверки. С какой ступени
начинать ход, считает `entry_step(changed)` по реестру; дальше поднимает repair-цикл (T-08).
Ступень 3 в `EscalationStep` нет — расширение поиска с разрешения появляется только после
зелёного гейта T-05.

## Коды нарушений

`severity ∈ {blocking, warning, suggestion}`. Только blocking останавливает выдачу плана.

| code | severity | repairable | действие |
|---|---|---|---|
| `BUDGET_EXCEEDED` | blocking | да | ступень 0 → `transport.overnight_ok`, дешёвые отели |
| `HARD_CONSTRAINT_UNSATISFIED` | blocking | зависит | если hard и не решается ступенями → **вопрос**, не ослабление |
| `ARRIVAL_AFTER_CHECKIN_DEADLINE` | blocking | да | трансфер / более ранний рейс / ранний заезд |
| `CHECKOUT_BEFORE_DEPARTURE` | blocking | да | сдвиг даты выезда |
| `TRANSFER_GAP_TOO_SHORT` | blocking | да | другой рейс или трансфер |
| `OCCUPANCY_EXCEEDED` | blocking | да | смена номера/отеля |
| `PET_NOT_ALLOWED` | blocking | да | фильтр `pet_friendly` |
| `CANDIDATES_EMPTY` | blocking | да | расширение, затем вопрос |
| `UNKNOWN_AVAILABILITY` | warning | да | пометить `unknown`, не выдавать за подтверждённое |
| `STALE_SOURCE` | warning | нет | показать дату проверки |
| `CONFLICTING_SOURCES` | warning | нет | обе цены + источник |
| `LEGAL_UNKNOWN` | warning | нет | «нужна проверка на официальном источнике», запрет на статус «подтверждённо» |
| `FABRICATED_FIELD` | blocking | нет | ошибка парсера → повтор с feedback |

Каждое нарушение: `expected`, `actual`, `affected_component`, `evidence`,
`suggested_actions` — из этого репейер берёт вход, а фронт рисует «что не так».

## SSE-контракт с фронтом

Фронту **не отдаётся текст как источник истины**: отдаётся `TripState` + поток событий,
текст — подпись к событию.

```
run_started          {run_id, trip_id}
phase_changed        {from, to}
llm_call             {slot, ms, tokens_in, tokens_out}      # опционально, для демо трейса
tool_started         {tool, args_hash, from_cache}
tool_completed       {tool, ms, count, status}
tool_failed          {tool, attempt, policy, will_retry}
question             {field, text, options[], allow_skip}   # ← interrupt оркестратора
candidates           {kind, items[], ranking_factors}
violation            {code, severity, expected, actual, affected_component}
repair_started       {violation_code, action_signature, iteration}
plan_updated         {version, changed_components[], kept_components[]}
answer_text          {block_id, text, numbers_verified}
checkout_completed   {order_id, idempotent_replay: bool, simulated: true}
budget_exhausted     {limit_hit, best_effort_version, unresolved[]}
```

## HTTP и возобновление после interrupt

```
POST   /v1/trips                          → trip_id
POST   /v1/trips/{id}/messages            → run_id, контент-тип text/event-stream
GET    /v1/trips/{id}/state?version=N     → TripState
POST   /v1/runs/{run_id}/answers          → возобновление прерванного хода
GET    /v1/runs/{run_id}                  → трейс фаз (для replay и для отладки)
POST   /v1/trips/{id}/checkout            → Idempotency-Key
```

Ход, остановленный на вопросе, живёт в БД как `run(status=interrupted, awaiting=field[])`,
а не в памяти процесса: перезапуск API не должен терять начатый разговор. Ответ на вопрос
идемпотентен по `(run_id, field)` — двойная отправка кнопки не создаёт второй ход и не
сдвигает `version` дважды. Фронт обязан уметь отрисовать «ожидает ответа» по `state`, а не
по наличию текста в ленте.

## Терминальные исходы (не исключения)

Каждый ход заканчивается одним из статусов, и все они — валидный ответ пользователю:

| Статус | Когда | Что видит пользователь |
|---|---|---|
| `plan_ready` | 0 blocking, violations = warnings | план + что не подтверждено |
| `awaiting_answer` | конфликт hard-ограничений или значимый unknown | вопрос с посчитанными компромиссами |
| `best_effort` | лимиты хода исчерпаны | лучший допустимый вариант + список неисследованного |
| `infeasible` | допустимое множество пусто и расширение невозможно без изменения требований | почему именно не складывается, по строкам `BudgetLedger`/ стыковок |
| `degraded` | источник лежит, выдаём stale | план + дата проверки + `stale` на затронутых полях |
| `unsupported` | запрос вне продукта (визовые гарантии, оплата, обход ограничений) | что мы не делаем и куда смотреть официальный источник |

`infeasible` и `best_effort` обязаны содержать **числа**, объясняющие отказ: какая строка
бюджета какая, до скольки нужно снизиться. «Не нашлось вариантов» без цифр — это провал на
невиданном запросе, а жюри будет давать именно их.

## Мягкая релевантность (слот №3)

Исполняется cross-encoder'ом (`/rerank`), а не чат-моделью: «атмосферный» не выражается
полем, и скоринть его LLM-вызовом дороже, чем один запрос к реранкеру
(`docs/LLM-BOUNDARY.md`, таблица 6 слотов — «не LLM по возможности»).

```python
def soft_relevance(self, query: str, documents: dict[str, str]) -> dict[str, float] | None
```

* `query` собирает код: `pref_text` плюс слова `themes` переводом (`beach` → «море пляж»),
  потому что реранкер понимает русский, а enum-значение — нет.
* `documents` — индексируемый текст кандидата под его `candidate_id`: название, звёзды,
  направление, удобства. Ключом, не позицией: сопоставление ответа с входом делает клиент,
  порядок в ответе перепутать нельзя.
* `None` — слот выключен, ключа нет или источник не ответил. Тогда порядок кандидатов
  остаётся тем, как его отдал `hotel_search`: доступность внешней модели не должна влиять на
  воспроизводимость трейса прогона.
* Число кладётся в `ref["relevance"]` кандидата отеля **после нормализации в пределах одного
  вызова** и округления до 0.05. Нормализация обязательна, потому что шкала у cross-encoder'ов
  своя: один отдаёт 0.001–0.02 на русских текстах, другой 0.3–0.5. Сырое число без приведения
  легло бы в один bucket с нулём, и слот №3 перестал бы менять план, оставаясь оплаченным.
  Минимум вызова → 0.05, максимум → 1.00, между ними линейно, затем шаг 0.05. Нижняя граница
  0.05 не случайна: у неоценённого кандидата балла нет, и `_hotel_order` читает за него 0.0 —
  значит оценённый обязан остаться выше, даже когда модель скорит почти нулём.
* Если все скоры вызова равны, каждый получает 0.50: различимого сигнала нет, и среди
  кандидатов снова решает стоимость ночи.
* `score` источника не перезаписывается: в состоянии остаются оба числа, и видно, что
  именно добавил слот №3.
* Отель в `compose` выбирается по ключу `(-relevance, стоимость ночи)`: мягкое предпочтение
  важнее цены среди подходящих, а превышение бюджета по-прежнему ловит валидатор — мягкое
  поле не превращается в тихий отказ от пожелания. Когда `relevance` нет ни у одного
  кандидата, ключ сводится к цене — ровно то поведение, на котором зелёны тесты T-05.
* В `budgets.llm_calls` слот №3 не входит: лимит шести обращений — это чат-слоты. В
  `budgets.tokens` его токены считаются, потому что стоимость хода должна быть видна из
  состояния, а не только из логов.

## Прочее

Логи и поля кода — английский; пользовательские строки и `detail` для HTTP — русский.
`checkout_mock` идемпотентен по `Idempotency-Key`: повтор не создаёт второй заказ.
