"""Живой провайдер слотов: OpenAI-совместимый `/chat/completions` (AITunnel и любые другие).

Ключ и модель — из окружения, в репозиторий они не попадают. Файл `.env` в корне (в
`.gitignore`) читается сам, чтобы не требовать `pydantic-settings`, которого у нас тоже нет:

    LLM_PROVIDER=aitunnel                     # mock → офлайн-моки T-05
    LLM_BASE_URL=https://api.aitunnel.ru/v1
    LLM_API_KEY=sk-aitunnel-…
    LLM_MODEL=<имя модели>
    LLM_MODEL_PARSE=<flash для слота №1>      # и так по слотам: QUESTIONS/FINALISTS/EXPLAIN/DIFF
    LLM_TEMPERATURE_EXPLAIN=0.2               # общая температура — 0, кроме SL-5
    LLM_REASONING_EFFORT=none                 # off — не слать параметр вовсе

Почему мок остаётся правильным ответом по умолчанию: гейт T-05 требует, чтобы тесты
проходили без сети, а `from_config()` при `LLM_PROVIDER=mock` возвращает `MockSlots`. Сигнатуры
совпадают с протоколом `LLMSlots`, поэтому рантайм провайдера не различает.

Что здесь решает, а что нет. Модель размечает сообщение (какое поле контракта, чьи слова,
hard или soft) — арифметику, типы, статусы `defaulted`/`inferred` и проверку, что значение
вообще было сказано, делает `schemas.apply_deltas`. После двух неудачной валидации схемы
идёт fallback-парсер (мок) и нарушение `FABRICATED_FIELD`, а не тихий дефолт: хуже тихого
дефолта только громкий ответ, собранной из несуществующих пожеланий.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel

from services.agent.app.llm.instructor import StructuredCaller
from services.agent.app.llm.schemas import (
    LEGAL_PATHS,
    ExplainSchema,
    FinalistsSchema,
    ParsedDelta,
    ParsedMessage,
    ParseSchema,
    QuestionSchema,
    apply_deltas,
)
from services.agent.app.llm.slots import Explanation, MockSlots
from services.agent.app.orchestration.policy import QuestionSpec
from shared.contracts.request import TripRequest
from shared.contracts.state import Candidate

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.aitunnel.ru/v1"
#: 20 с на весь ход, из них на один вызов модели — треть: остальные слоты и источники.
DEFAULT_TIMEOUT_S = 6.0
RETRIES = 2
#: Температура 0 — осознанный выбор, а не дефолт «по умолчанию»: нам нужна повторяемость
#: извлечения (и смысл кэша по content-hash), креативность в JSON-дельтах только ломает её.
DEFAULT_TEMPERATURE = 0.0
#: Потолок на ответ. Без него болтливая модель съедает бюджет хода: 700 токенов с запасом
#: перекрывают самый длинный список дельт, а `finish_reason=length` мы считаем неудачей слота.
DEFAULT_MAX_OUTPUT_TOKENS = 700
#: Потолок на сообщение пользователя. Сверх него текст не уходит в модель вовсе: разбор
#: берёт детерминированный парсер, а причина пишется в `failures`. Дышащая лимитами инъекция
#: «вот тебе 20k токенов инструкций» упирается именно сюда, а не в доброту промпта.
MAX_INPUT_CHARS = 1200
#: Reasoning-модели (deepseek-v4.1-flash*, qwen3.x-flash, glm-4.7-flash) на AITunnel тратят
#: весь `max_tokens` на скрытое размышление: ответ приходит с `content: null`,
#: `finish_reason: length` и 10k символов в поле `reasoning`, то есть слот формально жив, а
#: JSONa нет. Измерено 2026-10-27: `reasoning_effort="none"` выключает размышления, и тот же
#: запрос отдаёт валидный JSON за 2.4 с вместо 12 с и пустого ответа.
DEFAULT_REASONING_EFFORT = "none"

#: схема слота: без TypeVar `_structured` возвращал бы `Any`, и `model.text` в explain/diff
#: проходил бы как str, хотя опечатку в имени поля mypy уже не поймал бы.
SlotSchema = TypeVar("SlotSchema", bound=BaseModel)


class ProviderError(RuntimeError):
    """Провайдер не дал ответа: таймаут, сеть, HTTP >= 400 или непонятный payload.

    Отдельный класс, а не `RuntimeError` наугад, потому что обработка разная. Ошибку схемы
    мы показываем модели и просим исправить поля; сетевую показывать некому — шлюз висит
    молча (на непроверенном ключе AITunnel не отдаёт 401, а держит соединение до
    `ReadTimeout`). Поэтому `ProviderError` не выходит наружу из слота: значение берёт
    fallback-парсер, а в `failures` пишется `kind=provider`, чтобы в метрике «модель не
    смогла» не путалось с «шлюз не ответил».

    Повтора по сети здесь нет намеренно: при лимите 20 с на ход второй таймаут съедает весь
    бюджет, а висящий шлюз от повтора оживает редко — детерминированный фолбэк дешевле.
    """

#: Правила 2, 3 и 3a — не стилистика, а продолжение provenance-guard'а: гард сверяет `raw` с
#: сообщением и выбрасывает дельту целиком, поэтому нормализованная цитата («150000» вместо
#: «150к») и число, которого в сообщении нет, — это гарантированно потерянное hard-поле.
#: На живом прогоне 52 фраз 2026-10-27 такие отбраковки давали 11–13 на модель при точности
#: 96–97%. Обратная сторона измерена тем же прогоном: первая версия правила 3 запрещала `nights`
#: вовсе, и на 24 фразах пропало 10 названных словом ночей — поэтому запрещён только расчёт
#: ночей из дат, а сказанное «на 7 ночей» обязано доходить до контракта.
_SYSTEM_TEMPLATE = (
    "Ты извлекаешь требования к путешествию из русского сообщения. Верни ТОЛЬКО JSON вида\n"
    '{"deltas": [{"path": "...", "raw": "...", "kind": "hard|soft", '
    '"status": "known|inferred", "confidence": 0.0-1.0}], "note": ""}.\n'
    "Правила:\n"
    "1. path — строго из списка полей контракта, ниже.\n"
    "2. raw — дословная цитата из сообщения, без нормализации: «150к», а не «150000»; «вдвоём»,\n"
    "   а не «2»; «с 14 по 21 ноября», а не «2026-11-14». Цитату сверяют с сообщением, и её не\n"
    "   удалось найти — всё поле теряется.\n"
    "3. nights, lodging.rooms, budget.scope давай только если человек назвал их сам («на 7 ночей»,\n"
    "   «два номера», «на человека»). Ночи из дат не вычисляй: ночи считает код, и дельта «7» при\n"
    "   «с 14 по 21» цитату не проходит — поле теряется.\n"
    "3a. direction.mode не давай никогда: режим выводит код по тому, назван ли город.\n"
    "4. known — назвал сам; inferred — выведено из контекста (например «в ноябре» без числа).\n"
    "5. Чего не сказано — того нет: не подставляй значения по умолчанию, не додумывай города,\n"
    "   цены и даты. Лучше пустой список дельт, чем выдуманное поле.\n"
    "6. kind=hard — ограничение («не больше», «бюджет», «с собакой»); soft — пожелание.\n"
    "7. dates.start/dates.end давай ТОЛЬКО если в сообщении есть день месяца. «в ноябре» —\n"
    "   это не дата: либо не давай поле вовсе, либо status=inferred с confidence<=0.5.\n"
    "8. origin — город, ОТКУДА едут («из Москвы», «вылетаем из Казани»). Назначение в origin\n"
    "   класть нельзя: для него есть direction.explicit.\n"
    "9. «море», «горы», «пляж» как род отдыха — это themes (soft), а не direction и не деньги.\n"
    "   direction.explicit — только географическое название (Сочи, Казань, Калининград).\n"
    "10. budget.currency — только если валюта названа словом («в долларах», «в евро»); рублей в\n"
    "    сообщении нет не значит «не RUB»: поле просто не давай.\n"
    "11. party.children — цитата словами («с ребёнком», «двое детей»); возраст не выдумывай,\n"
    "    его подставит код и пометит как своё предположение.\n"
    "Поля контракта: [[FIELDS]]\n"
    "Сегодня [[TODAY]]. Год для дат бери из этого, месяц — из слов пользователя."
)


def parse_system_prompt(fields: list[str], today: date) -> str:
    """Промпт слота №1. Без `str.format`: в шаблоне лежит JSON-пример с фигурными скобками, и
    `.format` спотыкался о `{"deltas": …}` — падение происходило до всякого HTTP-запроса.
    """

    return _SYSTEM_TEMPLATE.replace("[[FIELDS]]", ", ".join(fields)).replace("[[TODAY]]", today.isoformat())


def load_env_file(path: Path | None = None) -> None:
    """`.env` в окружение, не перекрывая уже заданное. Уже заданное важнее файла.

    Без `python-dotenv`: формат — `KEY=value` и комментарии — разбирается в пять строк, а
    тянуть зависимость ради этого значит спорить с ТЗ.
    """

    env_path = path or Path.cwd() / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    logger.info("env file loaded path=%s", env_path)


def _env_float(name: str, default: float) -> float:
    """Число из окружения; мусор в переменной не должен ронять запуск демо."""

    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        logger.warning("env invalid name=%s value=%r — using default %s",
                       name, os.environ.get(name), default)
        return default


def _env_optional(name: str, default: str) -> str | None:
    """Строка из окружения, которую можно выключить.

    `LLM_REASONING_EFFORT=off` значит «не слать параметр вовсе» — для модели без размышлений,
    на которой он вызывает 400. Само значение `none` при этом легально и означает другое:
    «думать запрещено», а не «параметра нет».
    """

    value = os.environ.get(name, "").strip().lower()
    if value in {"off", "disable", "disabled", "no"}:
        return None
    return value or default


#: слот → переменные модели и температуры. Имена совпадают с номерами слотов на доске:
#: SL-1 parse, SL-2 questions, SL-4 finalists, SL-5 explain, SL-6 diff.
_SLOT_ENV = {
    "parse": ("LLM_MODEL_PARSE", "LLM_TEMPERATURE_PARSE"),
    "formulate": ("LLM_MODEL_QUESTIONS", "LLM_TEMPERATURE_QUESTIONS"),
    "finalists": ("LLM_MODEL_FINALISTS", "LLM_TEMPERATURE_FINALISTS"),
    "explain": ("LLM_MODEL_EXPLAIN", "LLM_TEMPERATURE_EXPLAIN"),
    "diff": ("LLM_MODEL_DIFF", "LLM_TEMPERATURE_DIFF"),
}


def _slot_routes(model: str, temperature: float) -> dict[str, tuple[str, float]]:
    """Слотные переопределения. Пустое значение или совпадающее с общей — не маршрут."""

    routes: dict[str, tuple[str, float]] = {}
    for slot, (model_env, temperature_env) in _SLOT_ENV.items():
        chosen = os.environ.get(model_env, "").strip()
        if not chosen or chosen == model:
            continue
        routes[slot] = (chosen, _env_float(temperature_env, temperature))
    return routes


class OpenAICompatibleSlots:
    """Пять LLM-слотов поверх OpenAI-совместимого API; мок остаётся фолбэком.

    Методы соответствуют протоколу `LLMSlots`: `parse`, `formulate`, `pick_finalists`,
    `explain`, `diff`. Никакого кэша здесь нет: счётчик вызовов хода ведёт рантайм, а лимиты
    (`llm_calls`) останавливают ход раньше, чем мы успеем разориться на повторах.

    Модель и температура могут быть свои на каждый слот (`slot_models`): SL-1/2/6 — дешёвый
    flash на извлечении, SL-5 — сильная модель на русском объяснении. Один HTTP-клиент на все
    слоты: ключ, base URL и таймаут у них общие, differ только тело запроса.
    """

    def __init__(self, *, model: str, api_key: str,
                 base_url: str = DEFAULT_BASE_URL,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 temperature: float = DEFAULT_TEMPERATURE,
                 slot_models: Mapping[str, tuple[str, float]] | None = None,
                 max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                 json_mode: bool = True,
                 reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
                 year: int | None = None) -> None:
        if not api_key:
            raise ValueError("api_key is required for the live provider")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.slot_models: dict[str, tuple[str, float]] = dict(slot_models or {})
        self.max_output_tokens = max_output_tokens
        self.json_mode = json_mode
        self.reasoning_effort = reasoning_effort
        self.year = year or date.today().year
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=self.timeout_s,
        )
        self._fallback = MockSlots()
        #: причины, по которым живой ответ пришлось заменить моком; видно в метрике T-07
        self.failures: list[dict[str, Any]] = []
        #: модель → параметры, которые шлюз отверг именно у неё: повторять заведомо
        #: неподдерживаемый параметр на каждом вызове — лишние секунды на ходу
        self._rejected_params: dict[str, set[str]] = {}

    def _route(self, slot: str) -> tuple[str, float]:
        """Модель и температура слота: слотная переменная важнее общей, общей нет — дефолт."""

        return self.slot_models.get(slot, (self.model, self.temperature))

    @classmethod
    def from_config(cls) -> Any:
        """`LLM_PROVIDER` решает, живой слот или офлайн-мок: один конфиг — два поведения.

        Дефолтной модели в репозитории нет намеренно: у всех участников свой ключ и свой
        набор. `LLM_MODEL` обязателен, когда включён живой режим, а слотные `LLM_MODEL_*`
        позволяют развести flash на извлечении и сильную модель на объяснении.
        """

        load_env_file()
        provider = os.environ.get("LLM_PROVIDER", "mock").strip().lower()
        if provider in {"", "mock", "offline"}:
            return MockSlots()
        model = os.environ.get("LLM_MODEL", "").strip()
        api_key = (os.environ.get("LLM_API_KEY") or os.environ.get("AITUNNEL_API_KEY") or "").strip()
        if not model or not api_key:
            raise ValueError(f"LLM_PROVIDER={provider} needs LLM_MODEL and LLM_API_KEY")
        return cls(
            model=model,
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            #: локальная модель на первом запросе грузит веса в RAM — 6 с ей не хватает, а
            #: «ожидание>20 c» на контур хода всё равно работает, поэтому лимит из окружения
            timeout_s=_env_float("LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S),
            temperature=_env_float("LLM_TEMPERATURE", DEFAULT_TEMPERATURE),
            slot_models=_slot_routes(model, _env_float("LLM_TEMPERATURE", DEFAULT_TEMPERATURE)),
            max_output_tokens=int(_env_float("LLM_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)),
            json_mode=os.environ.get("LLM_JSON_MODE", "1").strip().lower() not in {"0", "off", "false"},
            #: без этого deepseek-v4.1-flash и qwen3.x-flash отдают пустой content: весь
            #: бюджет токенов уходит в скрытое размышление (замер 2026-10-27)
            reasoning_effort=_env_optional("LLM_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
        )

    def close(self) -> None:
        self._client.close()

    # --- транспорт ---------------------------------------------------------

    def _complete(self, messages: list[dict[str, str]], *, slot: str = "pending") -> str:
        """Один логический вызов модели: POST /chat/completions с текстом в ответе.

        Любая неудача транспорта превращается в `ProviderError`: без этого `httpx.ReadTimeout`
        вылетал из `_structured` → `parse_verbose` → `run_cases` и обрывал прогон на первом
        же таймауте, вместо того чтобы отдать одно сообщение fallback-парсеру.

        Потолок `max_tokens` стоит не для экономии, а для предсказуемости: обрезанный на
        середине JSON верхний слой не увидит никогда — `finish_reason=length` считается
        неудачей слота здесь же.
        """

        model, temperature = self._route(slot)
        rejected = self._rejected_params.setdefault(model, set())
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_output_tokens,
        }
        #: Порядок деградации: `response_format` первым. Так измерено на AITunnel 2026-10-27:
        #: Qwen-flash отвечают 400 именно на него (с `reasoning_effort=none` тот же запрос с
        #: json_object проходит), а `reasoning_effort` отвергают старые модели без размышлений —
        #: им он и не нужен, json-гарантия для них важнее.
        knobs: list[str] = []
        if self.json_mode and "response_format" not in rejected:
            body["response_format"] = {"type": "json_object"}
            knobs.append("response_format")
        if self.reasoning_effort and "reasoning_effort" not in rejected:
            body["reasoning_effort"] = self.reasoning_effort
            knobs.append("reasoning_effort")
        pending = list(knobs)
        dropped: list[str] = []
        #: 400 при включённых параметрах разбирается без текста: шлюзы формулируют отказ
        #: по-разному (Aliyun пишет «'messages' must contain the word 'json'» и не упоминает
        #: `response_format` вовсе). Один повторный запрос стоит дешевле, чем недоступный весь
        #: слот parse на модели, которая просто не знает этого параметра.
        for _ in range(len(pending) + 1):
            response, elapsed_ms = self._post(body, model=model, slot=slot)
            if response.status_code < 400:
                rejected.update(dropped)
                return self._payload(response, elapsed_ms, model=model, slot=slot)
            logger.warning("llm http_error model=%s slot=%s status=%d ms=%d",
                           model, slot, response.status_code, elapsed_ms)
            if response.status_code != 400 or not pending:
                break
            name = pending.pop(0)
            body.pop(name, None)
            dropped.append(name)
            logger.warning("llm param_rejected model=%s slot=%s param=%s — retrying without it",
                           model, slot, name)
        #: ни один набор не прошёл — дело не в параметрах (ключ, имя модели, шлюз). Пометку
        #: `_rejected_params` не ставим: следующий прогон на живой модели обязан снова
        #: попросить json_object, а не молча надеяться на удачу.
        raise ProviderError(f"provider returned {response.status_code}: {response.text[:200]}")

    def _post(self, body: dict[str, Any], *, model: str,
              slot: str) -> tuple[Any, int]:
        """Чистый транспорт: сеть → `ProviderError`, ответ наружу без разбора.

        Мс возвращаются вместе с ответом: латентность считается вокруг запроса, а не вокруг
        разбора тела, иначе обрезанный ответ выглядел бы «быстрым».
        """

        started = time.monotonic()
        try:
            response = self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as error:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning("llm transport_error model=%s slot=%s error=%s ms=%d",
                           model, slot, type(error).__name__, elapsed_ms)
            raise ProviderError(f"{type(error).__name__}: {error}") from error
        return response, int((time.monotonic() - started) * 1000)

    def _payload(self, response: Any, elapsed_ms: int, *, model: str, slot: str) -> str:
        """Тело ответа → текст content: непонятный payload и обрезка = `ProviderError`."""

        try:
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            logger.warning("llm bad_payload model=%s slot=%s error=%s",
                           model, slot, type(error).__name__)
            raise ProviderError(f"malformed chat completion: {error}") from error
        if finish_reason == "length":
            raise ProviderError(f"completion truncated at max_tokens={self.max_output_tokens}")
        if not content:
            #: бывает у reasoning-моделей: ответ уехал в `reasoning_content`, а `content` — null
            raise ProviderError("empty completion (content is null)")
        usage = payload.get("usage") or {}
        logger.info("llm call model=%s slot=%s ms=%d prompt_tokens=%s completion_tokens=%s",
                    model, slot, elapsed_ms,
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return str(content)

    def _structured(self, schema: type[SlotSchema], *, system: str, user: str,
                    slot: str) -> tuple[SlotSchema | None, str]:
        """Схема слота и причина отказа: "" | "schema_failed" | "provider_unavailable".

        Модель или причина — никогда оба сразу, поэтому вызывающему хватает `if model is None`,
        а метрика T-07 по причине отличает «шлюз не ответил» от «вывод не прошёл схему».
        Тексты ошибок остаются в `self.failures`: в `parsed` они только замусорили бы строку.
        """

        try:
            result = StructuredCaller(
                lambda messages: self._complete(messages, slot=slot), retries=RETRIES
            ).call(schema, system=system, user=user)
        except (ProviderError, httpx.HTTPError) as error:
            #: `httpx.HTTPError` — страховка: `_complete` умеет превращать сеть в
            #: `ProviderError`, но подкласс или подмена транспорта в тесте могут бросить
            #: оригинальную ошибку, и она не имеет права валить прогон.
            self.failures.append({"slot": slot, "errors": [str(error)], "attempts": 0,
                                  "kind": "provider"})
            logger.warning("llm provider_unavailable slot=%s error=%s", slot, str(error)[:200])
            return None, "provider_unavailable"
        if result.model is None:
            self.failures.append({"slot": slot, "errors": result.errors,
                                  "attempts": result.attempts, "kind": "schema"})
            logger.warning("llm schema_failed slot=%s attempts=%d errors=%s",
                           slot, result.attempts, "; ".join(result.errors)[:200])
            return None, "schema_failed"
        #: `StructuredResult.model` — `BaseModel | None`: caller знает схему, а generic-датакласс
        #: в нашем instructor без `cast` не живёт. Проверка типов здесь и так пройдена:
        #: экземпляр создан `schema.model_validate`.
        return cast(SlotSchema, result.model), ""

    # --- слот №1: parse ----------------------------------------------------

    def parse_verbose(self, message: str) -> ParsedMessage:
        """Слот №1 с полной картиной: дельты, отбраковка, был ли фолбэк.

        Метрики T-07 считаются отсюда — `parse` возвращает только `TripRequest`, и по нему
        не видно, сколько значений отбросил provenance-guard.
        """

        system = parse_system_prompt(
            [_describe_path(path) for path in _legal_paths()], date(self.year, 1, 1)
        )
        if len(message) > MAX_INPUT_CHARS:
            #: в модель текст сверх потолка не уходит: prompt-инъекция длиной в роман стоит
            #: столько же, сколько отказ её обрабатывать, а пользователю нужен разбор, а не 500
            self.failures.append({"slot": "parse", "errors": [f"input_too_long: {len(message)} chars"],
                                  "attempts": 0, "kind": "input_limit"})
            logger.warning("llm input_rejected slot=parse chars=%d limit=%d",
                           len(message), MAX_INPUT_CHARS)
            return apply_deltas([], message, year=self.year,
                                fallback=self._fallback.parse(message[:MAX_INPUT_CHARS]),
                                fallback_reason="input_too_long")
        model, reason = self._structured(ParseSchema, system=system, user=message, slot="parse")
        if model is None:
            #: в `failures` причина уже записана `_structured` — дублировать её второй строкой
            #: смысла нет, зато счётчик «одна запись на один неудачный вызов слота» остаётся
            #: честным. Сам факт фолбэк-разбора виден в `fallback_reason`.
            return apply_deltas([], message, year=self.year, fallback=self._fallback.parse(message),
                                fallback_reason=reason)
        deltas = [delta for delta in model.deltas if isinstance(delta, ParsedDelta)]
        return apply_deltas(deltas, message, year=self.year)

    def parse(self, message: str) -> TripRequest:
        return self.parse_verbose(message).request

    # --- слот №2: formulate ------------------------------------------------

    def formulate(self, spec: QuestionSpec) -> str:
        """Слот №2: числа и поле уже посчитаны кодом, модель даёт только формулировку.

        Опции и `why` приходят входем — в ответе им неоткуда взяться выдуманным. Просим модель
        вернуть только `text`: `options` в схеме есть, но рантайм берёт формулировку и показывает
        пользователю варианты из `QuestionSpec`, поэтому второй список вариантов — это лишние
        токены и лишний шанс назвать вариант, которого во входе не было.
        """

        system = (
            "Ты формулируешь ОДИН уточняющий вопрос по-русски. Верни ТОЛЬКО JSON "
            '{"text": "..."}.\n'
            "Спрашивай ровно то поле, которое указано в `field`, и опирайся на `why`: человек "
            "должен понять, зачем вопрос.\n"
            "Новых фактов, чисел, цен, дат и вариантов ответа не добавляй — в данных их нет, и "
            "спрашивать можно только то, что скажет сам человек.\n"
            "Одно-два коротких предложения, без извинений и без «конечно!»."
        )
        user = json.dumps({
            "field": spec.field,
            "why": spec.why,
            "options": list(spec.options),
            "evidence": spec.evidence,
        }, ensure_ascii=False)
        model, _ = self._structured(QuestionSchema, system=system, user=user, slot="formulate")
        if model is None or not model.text.strip():
            return self._fallback.formulate(spec)
        return model.text

    # --- слот №4: pick_finalists ------------------------------------------

    def pick_finalists(self, candidates: list[Candidate]) -> list[Candidate]:
        """Слот №4: выбор из отфильтрованного кодом набора. Порядок задаёт источник, не модель.

        Модель называет максимум три `candidate_id`; любого кандидата вне входа она выдумать
        не сможет — неизвестный id отбрасывается здесь же, и вызывается фолбэк мока.
        """

        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        if not by_id:
            return []
        system = (
            "Выбери до 3 кандидатов из переданного списка и объясни каждого одной строкой. "
            "Верни ТОЛЬКО JSON {\"picked\": [candidate_id], \"rationale\": {id: текст}}.\n"
            "Использовать можно только id из входа: неизвестный id отбрасывается, и подбор "
            "уходит в детерминированный фолбэк.\n"
            "В `rationale` — название и причину из входа своими словами, БЕЗ чисел: цены, "
            "часы и проценты не пересказывай и не пересчитывай. Причина без числа надёжнее, "
            "чем число, которого нет в данных."
        )
        user = json.dumps({
            "candidates": [
                {"candidate_id": candidate.candidate_id, "kind": candidate.kind,
                 "ref": candidate.ref, "score": candidate.score}
                for candidate in candidates
            ]
        }, ensure_ascii=False)
        model, _ = self._structured(FinalistsSchema, system=system, user=user, slot="finalists")
        if model is None:
            return self._fallback.pick_finalists(candidates)
        picked = [by_id[item] for item in model.picked if item in by_id][:3]
        if not picked:
            logger.warning("llm finalists_unknown_ids slot=finalists ids=%s", model.picked)
            return self._fallback.pick_finalists(candidates)
        finalists: list[Candidate] = []
        for candidate in picked:
            reason = model.rationale.get(candidate.candidate_id, "")
            ref = dict(candidate.ref)
            if reason:
                ref["why"] = reason
            finalists.append(candidate.model_copy(update={"ref": ref}))
        return finalists

    # --- слот №5: explain --------------------------------------------------

    def explain(self, explanation: Explanation) -> str:
        """Слот №5: текст по посчитанным числам. Guard рантайма проверит числа после нас.

        Промпт запрещает арифметику и округление не потому, что модель считает плохо: «около
        150 тысяч» и «~3 000 ₽ за ночь» — это числа, которых нет в состоянии, и `unverified_numbers`
        такую строку отбраковывает. Значит ход тратит перегенерацию, а при второй неудаче
        отдаёт шаблон — пользователь видит суше, чем мог бы. Правило здесь ровно одно: числа
        только в том виде, в каком они пришли на входе.
        """

        system = (
            "Напиши по-русски короткое объяснение подборки (2-4 предложения) по переданным "
            'данным. Верни ТОЛЬКО JSON {"text": "..."}.\n'
            "Числа: используй только значения из данных и ровно в том виде, в каком они пришли. "
            "Не складывай, не вычитай, не дели и не округляй: вместо «около 150 тысяч» — "
            "«125 200 ₽», а нет готового числа — напиши без числа. Даты называй днями месяца "
            "из check_in/check_out, год не упоминай.\n"
            "Не добавляй новых фактов: ни про акции, ни про рейтинги, ни про погоду — их в "
            "данных нет."
        )
        user = explanation.model_dump_json()
        model, _ = self._structured(ExplainSchema, system=system, user=user, slot="explain")
        if model is None or not model.text.strip():
            return self._fallback.explain(explanation)
        return model.text

    # --- слот №6: diff -----------------------------------------------------

    def diff(self, old_plan: dict[str, str], new_plan: dict[str, str]) -> str:
        """Слот №6 помечает, что изменилось, и описывает это одной фразой.

        Различия считает код (два dict на входе), поэтому модель здесь нужна ровно для
        человеческой строки — и только.
        """

        changed = [component for component in new_plan
                   if component in old_plan and old_plan[component] != new_plan[component]]
        if not changed:
            return self._fallback.diff(old_plan, new_plan)
        system = (
            "Опиши по-русски ОДНУ строку: что в плане изменилось и что осталось. Верни "
            'ТОЛЬКО JSON {"text": "..."}.\n'
            "Назови каждый пункт из `changed` его же словом и укажи, на что он сменился "
            "(было → стало); пункты из `kept` перечисли одним списком.\n"
            "Новых фактов и чисел нет: только пересказ переданных различий. Цены и часы, "
            "которых во входе нет, не пиши."
        )
        user = json.dumps({"changed": {item: f"{old_plan[item]} -> {new_plan[item]}"
                                       for item in changed},
                           "kept": [item for item in new_plan if item not in changed]},
                          ensure_ascii=False)
        model, _ = self._structured(ExplainSchema, system=system, user=user, slot="diff")
        if model is None or not model.text.strip():
            return self._fallback.diff(old_plan, new_plan)
        return model.text


def slots_from_config() -> Any:
    """Точка входа для оркестратора: моки или живой провайдер — решает окружение.

    Возвращает `LLMSlots`, поэтому `Orchestrator(slots=slots_from_config())` не различает,
    кто разбирает сообщение. Именно здесь контур уходит с моков на модели, и именно здесь
    этот переход нельзя сделать дефолтом: тесты T-05/T-08 обязаны проходить без сети.
    """

    return OpenAICompatibleSlots.from_config()


def _legal_paths() -> list[str]:
    return sorted(LEGAL_PATHS)

def _describe_path(path: str) -> str:
    """Путь поля с подсказкой типа: модель должна понимать, чем `dates.start` отличается от `nights`."""

    hints = {
        "origin": "город выезда, строка",
        "nights": "число ночей, int",
        "direction.explicit": "названное направление, строка",
        "direction.excluded": "исключённые направления, список строк через запятую",
        "direction.mode": "explicit|open|forbidden",
        "dates.start": "дата заезда, ISO YYYY-MM-DD",
        "dates.end": "дата выезда, ISO YYYY-MM-DD",
        "dates.flexibility_days": "гибкость дат в днях, int 0..7",
        "party.adults": "число взрослых, int",
        "party.children": "возраст детей, список int через запятую",
        "party.pets": "есть ли питомец, правда/ложь",
        "transport.modes": "plane|train|auto через запятую",
        "transport.max_hours_direct": "максимум часов в пути, число",
        "transport.prefer_direct": "только прямые рейсы, правда/ложь",
        "transport.overnight_ok": "ночной переезд допустим, правда/ложь",
        "lodging.rooms": "число комнат, int",
        "lodging.stars_min": "минимум звёзд, int",
        "lodging.budget_per_night_max": "цена за ночь максимум, деньги",
        "lodging.must": "обязательные удобства, список строк",
        "identity.citizenship": "гражданство, строка",
        "identity.residence": "страна проживания, строка",
        "budget.amount": "бюджет, деньги",
        "budget.currency": "валюта, 3 буквы",
        "budget.scope": "total|per_person",
        "budget.includes": "что входит: transport,lodging,activities,transfer",
        "themes": "beach|city|nature|culture|food через запятую",
    }
    return f"{path} ({hints.get(path, 'строка')})"
