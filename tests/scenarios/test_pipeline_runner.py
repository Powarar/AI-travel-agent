"""Офлайн-гейт прогона: `scripts/run_pipeline.py` проходит весь контур без сети.

Скрипт существует для живых замеров, но его собственная проверка — бесплатная: если контур не
сходится на моках, на живой модели ошибка будет выглядеть как «модель виновата». Плюс здесь же
закрывается главный риск обёртки: `MeasuredSlots` обязан пробрасывать `tokens_used`, иначе ход
снова показывал бы ноль токенов, и всплыло бы это только в счёте.

Ни одного сетевого вызова: `--provider mock` подменяет и чат-слоты, и слот №3 (мок отдаёт
`None`), а про живой провайдер здесь только печать маршрутов. Гибрид `--rerank-only` проверяется
подменой `Reranker.from_config`: имя модели в таблице и в JSON настоящие, клиент — подстава.
"""

from __future__ import annotations

import json
from typing import cast

import pytest

from scripts import run_pipeline as rp
from services.agent.app.llm.rerank import RerankHit, Reranker
from services.agent.app.llm.slots import MockSlots
from services.agent.app.orchestration.runtime import Runtime


class CountingSlots(MockSlots):
    """Мок, у которого есть счётчик токенов: так выглядит живой провайдер для обёртки."""

    tokens_used = 120


class TestMeasuredSlotsWrapper:
    def test_tokens_are_passed_through_from_the_inner_provider(self) -> None:
        measured = rp.MeasuredSlots(CountingSlots())
        assert measured.tokens_used == 120
        assert measured.inner.tokens_used == 120

    def test_every_slot_call_is_counted_and_annotated(self) -> None:
        measured = rp.MeasuredSlots(CountingSlots())
        assert measured.soft_relevance("море", {"AYT-1": "отель"}) is None
        assert measured.notes["soft_relevance"] == ["нет скоров — порядок источника"]
        assert len(measured.ms["soft_relevance"]) == 1

    def test_unmeasured_slots_have_no_calls_yet(self) -> None:
        measured = rp.MeasuredSlots(CountingSlots())
        assert set(measured.ms) == {name for name, _, _ in rp.SLOTS}
        assert measured.ms["diff"] == []


class TestOfflineWalk:
    def test_answers_are_taken_from_flags_and_the_plan_is_built(self, capsys) -> None:
        code = rp.main(["--provider", "mock", "--answer", "origin=Москва",
                        "--answer", "dates.start=с 14 по 21"])
        out = capsys.readouterr().out
        assert code == 0
        assert "guard: чист на 1 из 1" in out
        assert "SL-3 relevance" in out
        assert " ord-AYT-AYT-FAM" in out

    def test_walk_uses_the_state_awaiting_fields_not_a_hardcoded_list(self) -> None:
        runtime = Runtime(rp.build_sources(), slots=rp.MeasuredSlots(CountingSlots()))
        report = rp.TurnReport()
        rp.walk(runtime, rp.DEFAULT_MESSAGE, {"origin": "Москва", "dates.start": "с 14 по 21"},
                now=rp.FIXTURE_NOW, ask=lambda path, question: "Москва", report=report)
        assert [path for path, _ in report.answers] == ["origin", "dates.start"]
        assert report.failed is None
        assert report.total == "125 200 ₽"
        assert report.components["hotel"] == "AYT-FAM"

    def test_missing_answer_is_a_clear_stop_not_a_traceback(self) -> None:
        def refuse(path: str, question: str) -> str:
            raise SystemExit(f"no answer for {path}")

        runtime = Runtime(rp.build_sources(), slots=rp.MeasuredSlots(CountingSlots()))
        report = rp.TurnReport()
        with pytest.raises(SystemExit, match="no answer for origin"):
            rp.walk(runtime, rp.DEFAULT_MESSAGE, {}, now=rp.FIXTURE_NOW, ask=refuse, report=report)

    def test_malformed_answer_flag_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            rp._answers_from_args(["origin"])

    def test_date_of_the_run_is_the_one_the_golden_numbers_were_counted_on(self) -> None:
        # 125 200 ₽ из ассертов T-05 посчитаны для этой даты; на реальной дате цены фикстур
        # разошлись бы с тестом, и прогон перестал бы быть тем же самым сценарием
        assert (rp.FIXTURE_NOW.year, rp.FIXTURE_NOW.month) == (2026, 11)


    def test_json_answer_is_one_machine_readable_object(self, capsys,
                                                         monkeypatch) -> None:
        """`--json` — ответ фронту и замеру: на stdout только JSON, таблицы молчат.

        Проверяем то, что ломается в первую очередь: сериализуемость состояния целиком
        (`TripState` знает про `Decimal` и `date`), счётчик провайдера на уровне обёртки
        (`tokens_used` проброшен, а не потерян) и `null` там, где слот №3 не отвечал.
        """

        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        code = rp.main(["--provider", "mock", "--json", "--answer", "origin=Москва",
                        "--answer", "dates.start=с 14 по 21"])
        out = capsys.readouterr().out
        assert code == 0
        payload = json.loads(out)
        run = payload["runs"][0]
        assert run["outcome"] == "plan_ready"
        assert run["order_id"] == "ord-AYT-AYT-FAM"
        assert run["money"]["total"] == "125 200 ₽"
        assert run["state"]["request"]["nights"]["value"] == 7
        assert run["metrics"]["llm_calls"] == 5
        assert [item["relevance"] for item in run["relevance"][:1]] == [None]
        assert payload["summary"]["tokens_by_provider"] == 0  # моки не считают токены
        assert "guard: чист" not in out

    def test_dry_run_under_json_does_not_touch_the_gateway(self, capsys) -> None:
        code = rp.main(["--provider", "mock", "--json", "--dry-run"])
        out = capsys.readouterr().out
        assert code == 0
        assert json.loads(out)["dry_run"] is True


class TestCostVisibility:
    def test_mock_routing_prints_that_there_is_no_network(self, capsys) -> None:
        rp.print_routing(CountingSlots())
        assert "моки T-05" in capsys.readouterr().out

    def test_live_routing_lists_the_env_var_and_the_model_behind_it(self, capsys,
                                                                    monkeypatch) -> None:
        monkeypatch.setenv("LLM_MODEL", "qwen3.5-flash")
        monkeypatch.setenv("LLM_MODEL_PARSE", "gemini-3-flash")
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        rp.print_routing(object())  # не `MockSlots` ⇒ ветка живого провайдера, сети нет
        out = capsys.readouterr().out
        assert "gemini-3-flash" in out            # слотный переопределённый маршрут
        assert "qwen3.5-flash" in out             # общая модель для остальных
        assert "слот выключен" in out             # без RERANK_MODEL слот №3 не трогает шлюз
        assert "не вызывается" in out             # SL-6 появится с T-08


class StubReranker:
    """Реранкер-подстава: отдаёт порядок обратный входу, ни одного запроса к шлюзу."""

    model = "stub-reranker"

    def __init__(self) -> None:
        self.tokens_used = 7
        self.failures: list[dict[str, object]] = []
        self.closed = False
        self.calls: list[list[str]] = []

    def rank(self, query: str, documents: list[str],
             *, top_n: int | None = None) -> list[RerankHit]:
        self.calls.append(documents)
        #: последний по входу документ получает лучший балл — так видно, что ключ возврата
        #: берётся из позиции во ВХОДЕ, а не из порядка ответа шлюза
        return [RerankHit(index=index, score=float(len(documents) - position), text=document)
                for position, (index, document) in enumerate(
                    reversed(list(enumerate(documents))))]

    def close(self) -> None:
        self.closed = True


class TestRerankOnlyHybrid:
    """Гибрид SL-3: чат — моки T-05, живой только `/rerank`. Здесь проверяется офлайн-часть.

    Главный риск флага — не «плохой реранк», а то, что прогон, названный бесплатным, заплатит за
    чат или за дефолтную модель реранка. Поэтому проверки именно об этом: без `RERANK_MODEL`
    клиент не собирается вообще, а чат-слоты остаются моками при любом окружении.
    """

    def test_without_the_env_var_the_client_is_never_built(self, monkeypatch) -> None:
        """Без явного `RERANK_MODEL` слот выключен, даже если ключ в `.env` лежит.

        `Reranker.from_config` на пустом имени взял бы `DEFAULT_MODEL` и заплатил дефолтом
        молча — здесь он подменён так, чтобы любой вызов стал падением теста.
        """

        monkeypatch.delenv("RERANK_MODEL", raising=False)

        def explode(cls: type[Reranker]) -> Reranker:
            raise AssertionError("Reranker.from_config called without RERANK_MODEL")

        monkeypatch.setattr(Reranker, "from_config", classmethod(explode))
        hybrid = rp.build_slots("mock", rerank_only=True)
        assert isinstance(hybrid, rp.ChatMockRerankLive)
        assert hybrid.reranker is None

    def test_chat_slots_stay_the_mock_answers(self, monkeypatch) -> None:
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        hybrid = rp.build_slots("mock", rerank_only=True)
        mock = MockSlots()
        assert hybrid.parse(rp.DEFAULT_MESSAGE) == mock.parse(rp.DEFAULT_MESSAGE)
        assert hybrid.soft_relevance("море", {"AYT-1": "отель"}) is None
        assert hybrid.tokens_used == 0
        hybrid.close()  # закрывать нечего, и это не должно быть ошибкой

    def test_scores_are_keyed_by_candidate_id_not_position(self) -> None:
        stub = StubReranker()
        hybrid = rp.ChatMockRerankLive(cast(Reranker, stub))
        scores = hybrid.soft_relevance("бассейн и клуб для детей",
                                       {"AYT-STD": "Old Town Studio",
                                        "AYT-FAM": "Aqua Family Resort",
                                        "AYT-LUX": "Gulf Palace"})
        assert scores is not None
        assert set(scores) == {"AYT-STD", "AYT-FAM", "AYT-LUX"}
        assert scores["AYT-LUX"] > scores["AYT-FAM"] > scores["AYT-STD"]
        assert stub.calls == [["Old Town Studio", "Aqua Family Resort", "Gulf Palace"]]
        hybrid.close()
        assert stub.closed

    def test_tokens_from_the_reranker_reach_the_report(self) -> None:
        measured = rp.MeasuredSlots(rp.ChatMockRerankLive(cast(Reranker, StubReranker())))
        assert measured.tokens_used == 7

    def test_empty_or_blank_input_never_calls_the_gateway(self) -> None:
        stub = StubReranker()
        hybrid = rp.ChatMockRerankLive(cast(Reranker, stub))
        assert hybrid.soft_relevance("  ", {"AYT-1": "отель"}) is None
        assert hybrid.soft_relevance("море", {}) is None
        assert stub.calls == []

    def test_dry_run_says_which_track_is_paid(self, capsys, monkeypatch) -> None:
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        code = rp.main(["--provider", "mock", "--rerank-only", "--dry-run"])
        out = capsys.readouterr().out
        assert code == 0
        assert "chat=mock (бесплатно), rerank=выключен" in out
        assert "0 чат-вызовов" in out

    def test_dry_run_json_carries_the_same_routing_as_the_console(self, capsys,
                                                                  monkeypatch) -> None:
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        stub = StubReranker()
        monkeypatch.setattr(Reranker, "from_config",
                            classmethod(lambda cls: stub))  # имя настоящее, клиент подменный
        code = rp.main(["--provider", "mock", "--rerank-only", "--dry-run", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["routing"] == {"chat": "mock", "rerank": "stub-reranker"}
        assert payload["models"]["SL-3 relevance"] == "stub-reranker"
        assert payload["models"]["SL-1 parse"] == "mock (T-05)"
        assert stub.closed is True  # dry-run обязан закрыть собранный клиент

    def test_a_live_chat_provider_is_refused_rather_than_paid_for(self, capsys) -> None:
        code = rp.main(["--provider", "aitunnel", "--rerank-only", "--dry-run"])
        assert code == 2
        assert "чат — моки" in capsys.readouterr().err
