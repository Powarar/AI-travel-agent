"""Клиент `/rerank` проверяется без сети: форма запроса, разбор ответа и поведение при отказе.

Зачем это тестируется, хотя кода на двести строк:

* слот №3 стоит в критическом пути демо, а шлюз может не ответить прямо на сцене — значит
  «отказ = свой порядок кода» обязан быть проверен, а не надеется на `try`;
* форма запроса (`documents`, `top_n`) и форма ответа (`index`, `relevance_score`) взяты из
  живого вызова 2026-10-27, и без теста они живут только в памяти автора клиента;
* `index` — позиция во **входе**: перепутать её с позицией в ответе значит молча показать
  пользователю другой отель, и на глаз это не поймать.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from services.agent.app.llm.rerank import (
    MAX_DOCUMENTS,
    RerankError,
    Reranker,
    parse_rerank_response,
    prepare_documents,
)

DOCS = ["бутик на первой линии", "сетевой у аэропорта", "камерный в старом городе"]


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x.test/v1")


def _body(handler_request: httpx.Request) -> dict[str, Any]:
    return json.loads(handler_request.content.decode())


class TestRequestShape:
    def test_payload_matches_the_documented_shape(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(_body(request))
            return httpx.Response(200, json={"results": [
                {"index": 0, "relevance_score": 0.9}]})

        reranker = Reranker(api_key="k", client=_client(handler))
        reranker.rank("атмосферный отель у моря", DOCS, top_n=1)
        assert seen["query"] == "атмосферный отель у моря"
        assert seen["documents"] == DOCS
        assert seen["top_n"] == 1
        #: `model` подставляется в `_post`, а не в `rank`: именно так запасная модель
        #: переиспользует тот же payload, не собирая его заново
        assert seen["model"] == "qwen3-reranker-8b"

    def test_input_is_capped_before_the_call(self) -> None:
        """Длинные строки и хвост списка наружу не уходят: за каждую пару платят, и запрос
        размером с роман — это та же инъекция, только дорогая."""

        assert len(prepare_documents(["x" * 5000])[0]) == 400
        assert len(prepare_documents([str(i) for i in range(200)])) == MAX_DOCUMENTS
        handler = lambda request: httpx.Response(  # noqa: E731
            200, json={"results": [{"index": 0, "relevance_score": 0.5}]})
        captured: dict[str, Any] = {}

        def spy(request: httpx.Request) -> httpx.Response:
            captured.update(_body(request))
            return handler(request)

        reranker = Reranker(api_key="k", client=_client(spy))
        reranker.rank("вопрос" * 200, ["один"])
        assert len(captured["query"]) == 200

    def test_no_documents_no_call(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - не вызывается
            raise AssertionError("empty input must not reach the gateway")

        reranker = Reranker(api_key="k", client=_client(handler))
        assert reranker.rank("что-то", []) is None
        assert reranker.rank("   ", DOCS) is None


class TestResponseParsing:
    def test_hits_are_sorted_and_carry_input_indexes(self) -> None:
        body = {"results": [
            {"index": 2, "relevance_score": 0.02},
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.5},
        ]}
        hits = parse_rerank_response(body, documents=DOCS)
        assert [hit.index for hit in hits] == [0, 1, 2]
        assert hits[0].text == DOCS[0]

    def test_unknown_index_is_dropped_not_reused(self) -> None:
        hits = parse_rerank_response(
            {"results": [{"index": 7, "relevance_score": 0.99},
                         {"index": 1, "relevance_score": 0.1}]}, documents=DOCS)
        assert [hit.index for hit in hits] == [1]

    def test_missing_results_is_an_error(self) -> None:
        with pytest.raises(RerankError):
            parse_rerank_response({"object": "list"}, documents=DOCS)


class TestFailureIsolation:
    def _hangs(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("gateway hangs")

    def test_timeout_returns_none_and_keeps_the_reason(self) -> None:
        reranker = Reranker(api_key="k", client=_client(self._hangs))
        assert reranker.rank("отель", DOCS) is None
        assert reranker.failures
        assert all(item["kind"] == "provider" for item in reranker.failures)

    def test_fallback_model_is_the_second_attempt(self) -> None:
        """Первая модель легла — пробуем запасную; это один вызов `rank`, а не два прохода
        оркестратора, и в метрике видно, какая модель реально отработала."""

        models: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = _body(request)
            models.append(str(body["model"]))
            if len(models) == 1:
                raise httpx.ReadTimeout("first model is slow")
            return httpx.Response(200, json={"results": [{"index": 1, "relevance_score": 0.7}]})

        reranker = Reranker(api_key="k", client=_client(handler))
        hits = reranker.rank("отель у моря", DOCS)
        assert models == ["qwen3-reranker-8b", "rerank-3-lite"]
        assert hits is not None and hits[0].index == 1
        assert len(reranker.failures) == 1

    def test_http_error_is_recorded_as_response_kind(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        reranker = Reranker(api_key="k", fallback_model=None, client=_client(handler))
        assert reranker.rank("отель", DOCS) is None
        assert reranker.failures[0]["kind"] == "response"

    def test_garbage_json_does_not_escape(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": "not a list"})

        reranker = Reranker(api_key="k", fallback_model=None, client=_client(handler))
        assert reranker.rank("отель", DOCS) is None


class TestFromConfig:
    def test_off_flag_disables_the_slot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RERANK_MODEL", "off")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        assert Reranker.from_config() is None

    def test_missing_key_disables_the_slot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        #: `from_config` читает `.env` (это и есть поведение на проде), а на машине тестя
        #: рабочий ключ там лежит — поэтому файл подменяется noop'ом, и проверяется ровно
        #: ветка «ключа нет»: слот выключен, оркестратор остаётся на порядке кода.
        from services.agent.app.llm import rerank

        monkeypatch.setattr(rerank, "load_env_file", lambda: None)
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("AITUNNEL_API_KEY", raising=False)
        assert Reranker.from_config() is None

    def test_key_builds_a_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RERANK_MODEL", "")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        reranker = Reranker.from_config()
        assert isinstance(reranker, Reranker)
        assert reranker is not None and reranker.model == "qwen3-reranker-8b"
        reranker.close()

    def test_empty_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            Reranker(api_key="")
