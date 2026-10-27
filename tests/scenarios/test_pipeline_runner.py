"""Офлайн-гейт прогона: `scripts/run_pipeline.py` проходит весь контур без сети.

Скрипт существует для живых замеров, но его собственная проверка — бесплатная: если контур не
сходится на моках, на живой модели ошибка будет выглядеть как «модель виновата». Плюс здесь же
закрывается главный риск обёртки: `MeasuredSlots` обязан пробрасывать `tokens_used`, иначе ход
снова показывал бы ноль токенов, и всплыло бы это только в счёте.

Ни одного сетевого вызова: `--provider mock` подменяет и чат-слоты, и слот №3 (мок отдаёт
`None`), а про живой провайдер здесь только печать маршрутов.
"""

from __future__ import annotations

import pytest

from scripts import run_pipeline as rp
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
