"""Structured output без пакета `instructor`: тот же механизм в ~60 строк.

Доска provider integration требует «structured output с retry по validation-ошибке и фидбеком конкретных
полей». Готовый пакет мы поставить не можем (новые зависимости блоку запрещены), поэтому
здесь ровно то, за что он и нужен:

1. ответ модели вытаскивается из markdown-обёртки и парсится как JSON;
2. JSON валидируется Pydantic-схемой слота;
3. при `ValidationError` обратно в модель уходит **список полей с текстом ошибки** — не
   «попробуй ещё», а «`deltas.0.path`: unknown field path …», иначе ретрай повторяет ту же
   ошибку;
4. после `retries` неудач — `None`, и вызывающий идёт fallback-парсером с `FABRICATED_FIELD`.

Ошибок сети здесь нет намеренно: HTTP принадлежит провайдеру, этот слой знает только про
форму ответа.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(text: str) -> dict[str, object]:
    """Ответ модели → dict. Снимает код-блок и вырезает первое сбалансированное {…}.

    Модель регулярно отдаёт «Вот JSON: ```json {...} ```» — это не ошибка, это формат ответа,
    и терять из-за него валидный вывод нельзя.
    """

    stripped = _FENCE.sub("", text.strip())
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start == -1:
            raise ValueError(f"no JSON object in response: {text[:120]!r}") from None
        depth = 0
        for index in range(start, len(stripped)):
            if stripped[index] == "{":
                depth += 1
            elif stripped[index] == "}":
                depth -= 1
                if depth == 0:
                    parsed = json.loads(stripped[start:index + 1])
                    break
        else:
            raise ValueError(f"unbalanced JSON in response: {text[:120]!r}") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def field_errors(error: ValidationError) -> list[str]:
    """ValidationError → строки «path: message», которые можно отправить модели обратно."""

    lines: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "root"
        lines.append(f"{location}: {item['msg']}")
    return lines


@dataclass
class StructuredResult:
    """Итог вызова слота: схема или причины, по которым её не удалось получить."""

    model: BaseModel | None
    errors: list[str] = field(default_factory=list)
    attempts: int = 0


class StructuredCaller:
    """Валидирует вывод по схеме и один раз даёт модели шанс исправить свои поля.

    `complete(messages) -> str` инжектируется провайдером: здесь нет ни HTTP, ни ключа, ни
    ретраев по сети — только форма ответа.
    """

    def __init__(self, complete: Callable[[list[dict[str, str]]], str], *, retries: int = 2) -> None:
        self._complete = complete
        self._retries = retries

    def call(self, schema: type[BaseModel], *, system: str, user: str) -> StructuredResult:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        errors: list[str] = []
        for attempt in range(1, self._retries + 2):
            raw = self._complete(messages)
            try:
                payload = extract_json(raw)
            except ValueError as error:
                errors = [f"json: {error}"]
            else:
                try:
                    return StructuredResult(schema.model_validate(payload), errors=[], attempts=attempt)
                except ValidationError as error:
                    errors = field_errors(error)
            if attempt > self._retries:
                break
            messages = messages[:2] + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Твой ответ не прошёл проверку схемы. Исправь поля и "
                                             "верни только JSON.\n" + "\n".join(f"- {item}" for item in errors)},
            ]
        return StructuredResult(None, errors=errors, attempts=self._retries + 1)
