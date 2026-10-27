"""Клиент `/rerank` шлюза — слот №3 («мягкая релевантность») без LLM.

Три вещи, которые здесь решены явно, потому что они дороже кода:

1. **Ошибки наружу не выходят.** Реранк украшает порядок кандидатов, а не решает выполнимость:
   если шлюз висит, ход обязан отдать тот порядок, который посчитал код (`score` источника),
   а не падать и не становиться пустым. Поэтому `rank` возвращает `None`, а причину пишет в
   `failures` — ровно как в `provider_openai_compatible`, где «модель не смогла» отделено от
   «шлюз не ответил».
2. **Вход ограничен по объёму.** `documents` приходит из каталога, но `query` — строка
   пользователя, и она уходит в чужой эндпоинт. Потолок на длину запроса, на число документов и
   на длину документа — это и защита от инъекции размером с роман, и защита счёта: реранк
   платит за каждую пару, а 500 кандидатов никто не смотрит.
3. **Запасная модель — второй вызов, а не молчаливый дефолт.** В каталоге AITunnel
   `qwen3-reranker-8b` и `rerank-3-lite` — разные строки прайса; при таймауте первой пробуется
   вторая, и только потом рантайм остаётся на порядке кода. Два этих случая в метрике
   различать нужно, иначе «модель ранжирует плохо» и «моделей не было» сливаются в одно число.

Форму ответа шлюз отдаёт fireworks-совместимую: `{"results": [{"index", "relevance_score",
"document": {"text"}}], "usage": {...}}` — проверено живым вызовом 2026-10-27, `http=200`,
308 токенов на три документа. Порядок в `results` уже отсортирован по убыванию, но полагаться
на это нельзя: сортируем сами, а `index` сверяем с входом, чтобы не перепутать кандидата.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from services.agent.app.llm.provider_openai_compatible import (
    DEFAULT_BASE_URL,
    _env_float,
    load_env_file,
)

logger = logging.getLogger(__name__)

#: Переменные окружения: модель основная и запасная. `RERANK_MODEL=off` выключает слот целиком
#: — тогда `from_config` вернёт `None`, и вызывающий код останется на детерминированном
#: порядке. Это легальный режим для офлайн-тестов и для демо с выключенным шлюзом.
MODEL_ENV = "RERANK_MODEL"
FALLBACK_MODEL_ENV = "RERANK_MODEL_FALLBACK"
TIMEOUT_ENV = "RERANK_TIMEOUT_S"

DEFAULT_MODEL = "qwen3-reranker-8b"
DEFAULT_FALLBACK_MODEL = "rerank-3-lite"
#: 2.5 с при баре приёмки p95 ≤1 с: на живом шлюзе реранк отвечал 1.4–2.5 с. Ставить ровно бар
#: значит гнать каждый второй вызов в фолбэк и мерить пустоту — сначала числа, потом бюджет.
DEFAULT_TIMEOUT_S = 2.5

MAX_QUERY_CHARS = 200
MAX_DOCUMENTS = 64
MAX_DOCUMENT_CHARS = 400


class RerankError(RuntimeError):
    """Шлюз ответил, но ответ нельзя разобрать: нет `results`, чужие поля, неизвестный index."""


@dataclass(frozen=True)
class RerankHit:
    """Один проранжированный документ: позиция во ВХОДЕ, а не в ответе."""

    index: int
    score: float
    text: str


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


def prepare_documents(documents: list[str], *, limit: int = MAX_DOCUMENTS,
                      chars: int = MAX_DOCUMENT_CHARS) -> list[str]:
    """Кандидаты к вызову: не больше `limit` штук, каждый не длиннее `chars`.

    Отсечка по количеству — это «смотрим только начало списка», а порядок начала задаёт код по
    `score` источника, так что отрезанный хвост в топ-3 не попал бы и без реранка.
    """

    return [_clip(item, chars) for item in documents[:limit]]


def parse_rerank_response(body: dict[str, Any], *, documents: list[str]) -> list[RerankHit]:
    """`{"results": [...]}` → хиты, отсортированные по убыванию скора.

    `index` из ответа — позиция во входном списке, и нужна именно она: `text` шлюз возвращает
    только для отладки и на него полагаться нельзя. Хит с неизвестным index или без скора
    отбрасывается поштучно — лучше короче список, чем перепутанные кандидаты.
    """

    results = body.get("results")
    if not isinstance(results, list):
        raise RerankError("response has no `results` list")
    hits: list[RerankHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if not isinstance(index, int) or not 0 <= index < len(documents):
            continue
        if not isinstance(score, (int, float)):
            continue
        hits.append(RerankHit(index=index, score=float(score), text=documents[index]))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits


class Reranker:
    """Тонкий клиент `/rerank`: список строк на входе, проранжированные хиты на выходе."""

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str,
                 base_url: str = DEFAULT_BASE_URL, timeout_s: float = DEFAULT_TIMEOUT_S,
                 fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
                 client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("api_key is required for the rerank client")
        self.model = model
        self.fallback_model = fallback_model
        self.timeout_s = timeout_s
        #: незакрытый `httpx.Client` — соединение, живущее дольше хода; в тестах клиент
        #: подменяется, и закрывать чужой подменный нельзя
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        #: одна запись на один неудачный вызов; `kind` различает сеть и разбор ответа
        self.failures: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls) -> "Reranker | None":
        """Собирает клиент из окружения; `None` — если слот выключен или ключа нет.

        Дефолт — «не трогаем сеть»: тесты T-05/T-08 обязаны проходить без доступа к шлюзу, и
        включать внешний вызов может только явная переменная, а не наличие ключа в файле.
        """

        load_env_file()
        name = os.environ.get(MODEL_ENV, "").strip()
        if name.lower() in {"off", "disable", "disabled", "no"}:
            return None
        key = (os.environ.get("LLM_API_KEY") or os.environ.get("AITUNNEL_API_KEY") or "").strip()
        if not key:
            logger.warning("rerank disabled: no api key in environment")
            return None
        return cls(
            model=name or DEFAULT_MODEL,
            api_key=key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            timeout_s=_env_float(TIMEOUT_ENV, DEFAULT_TIMEOUT_S),
            fallback_model=os.environ.get(FALLBACK_MODEL_ENV, "").strip() or DEFAULT_FALLBACK_MODEL,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _post(self, model: str, payload: dict[str, Any]) -> tuple[list[RerankHit], int]:
        started = time.monotonic()
        response = self._client.post("/rerank", json={**payload, "model": model})
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            raise RerankError(f"http {response.status_code}: {response.text[:120]}")
        body = response.json()
        hits = parse_rerank_response(body, documents=payload["documents"])
        logger.info("rerank call model=%s docs=%d ms=%d tokens=%s",
                    model, len(payload["documents"]), elapsed_ms,
                    (body.get("usage") or {}).get("total_tokens"))
        return hits, elapsed_ms

    def rank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[RerankHit] | None:
        """Отранжировать `documents` относительно `query`. `None` — ранжировать нечем.

        Вызывающий код обязан на `None` оставить свой порядок: это не «пусто», а «мы не знаем
        лучше, чем уже посчитанный скор».
        """

        docs = prepare_documents([str(item) for item in documents])
        if not docs or not query.strip():
            return None
        payload = {
            "query": _clip(query, MAX_QUERY_CHARS),
            "documents": docs,
            "top_n": top_n if top_n is not None else len(docs),
        }
        for model in (self.model, self.fallback_model):
            if not model:
                continue
            try:
                hits, _ = self._post(model, payload)
            except (httpx.HTTPError, RerankError, ValueError) as error:
                #: сеть и разбор ответа пишут разные `kind`: на висящем шлюзе мы видели только
                #: `provider`, и путать это с «шлюз прислал чушь» значило бы чинить не то
                kind = "provider" if isinstance(error, httpx.HTTPError) else "response"
                self.failures.append({"model": model, "kind": kind,
                                      "error": f"{type(error).__name__}: {str(error)[:160]}"})
                logger.warning("rerank %s model=%s error=%s", kind, model, str(error)[:160])
                continue
            return hits
        return None
