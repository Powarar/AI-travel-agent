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
from services.agent.app.llm import provider_openai_compatible as provider_mod
from services.agent.app.llm import rerank as rerank_mod
from services.agent.app.llm.rerank import RerankHit, Reranker
from services.agent.app.llm.slots import MockSlots
from services.agent.app.orchestration.runtime import Runtime


def _no_env_file(path: object = None) -> None:
    """Заглушка читателя `.env`: файл разработчика не участвует в офлайн-гейте."""


#: Маршрут слотов целиком живёт в `.env`, а `rp.main()` вызывает `load_env_file()` сам. Значит
#: `monkeypatch.delenv("RERANK_MODEL")` в тесте ничего не отключает: setdefault возвращает
#: строку из файла, и прогон «посмотреть preflight» собирает живой реранкер — то есть тесты
#: зависели бы от того, что П1 оставил в локальном конфиге после замера. Здесь файл глушится,
#: а переменные очищаются; живой путь тесты включают только явным `setenv`.
_ROUTE_ENV = ("LLM_PROVIDER", "LLM_MODEL", "LLM_MODEL_PARSE", "LLM_MODEL_QUESTIONS",
              "LLM_MODEL_FINALISTS", "LLM_MODEL_EXPLAIN", "LLM_MODEL_DIFF",
              "LLM_TEMPERATURE", "LLM_TEMPERATURE_EXPLAIN", "RERANK_MODEL",
              "RERANK_MODEL_FALLBACK", "LLM_API_KEY", "AITUNNEL_API_KEY")


@pytest.fixture(autouse=True)
def hermetic_routing(monkeypatch):
    for module in (rp, provider_mod, rerank_mod):
        monkeypatch.setattr(module, "load_env_file", _no_env_file)
    for name in _ROUTE_ENV:
        monkeypatch.delenv(name, raising=False)


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

    def test_unreadable_answer_is_a_failed_run_not_a_traceback(self, capsys) -> None:
        """«2026-11-12» вместо «с 12 по 17» — прогон с результатом, а не падение узла.

        Так отвечает и человек в терминале, и живая модель слота №2. Скрипт меряет за деньги:
        трекбек посреди хода означал бы «заплачено, чисел нет», а фронт по `--json` получил бы
        невалидный JSON вместо ответа.
        """

        code = rp.main(["--provider", "mock", "--json", "--answer", "origin=Москва",
                        "--answer", "dates.start=2026-11-12"])
        captured = capsys.readouterr()
        assert code == 1 and captured.err == ""
        payload = json.loads(captured.out)
        run = payload["runs"][0]
        assert "dates.start" in run["failed"] and "не прочитан" in run["failed"]
        #: состояние останова отдаётся: сколько денег на этом входе уже сожжено
        assert run["metrics"]["llm_calls"] == 3
        assert [row["path"] for row in run["answers"]] == ["origin", "dates.start"]
        assert payload["summary"]["reached_plan"] == 0

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
        #: «0 чат-вызовов» больше не строчка в тексте: план считается прогоном моков, и в
        #: таблице видно столько, сколько слоты действительно сделают на этом входе
        assert "чат-вызовов на ход: 2 при лимите 6" in out
        assert "SL-3 relevance        0" in out

    def test_dry_run_json_carries_the_same_routing_as_the_console(self, capsys,
                                                                  monkeypatch) -> None:
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        #: ключ нужен и здесь: `/rerank` на этом флаге — оплачиваемый слот, и preflight
        #: требует ключ для любого платного пути. Раньше он приходил из `.env` разработчика
        #: невидимо для теста — ровно та зависимость, которую `hermetic_routing` убирает.
        monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
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


class TestPreflightAndCallPlan:
    """Готовность к платному прогону: проверка конфигурации и план вызовов до первого запроса.

    Живой прогон стоит денег, и «примерно по одному вызову на слот» для него — не план. Числа
    здесь считаются бесплатным прогоном моков на том же входе, а конфигурация проверяется до
    конструирования провайдера: `httpx.Client` с пустым ключом собирается молча и падает
    посреди уже оплаченного хода.
    """

    def test_the_plan_counts_calls_instead_of_guessing_them(self, capsys, monkeypatch) -> None:
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        code = rp.main(["--provider", "mock", "--rerank-only", "--dry-run", "--json",
                        "--repeat", "3", "--answer", "origin=Москва",
                        "--answer", "dates.start=с 14 по 21"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        plan = payload["plan"]
        assert plan["per_run"] == {"SL-1 parse": 1, "SL-2 question": 2, "SL-3 relevance": 1,
                                   "SL-4 finalists": 1, "SL-5 explain": 1, "SL-6 diff": 0}
        assert plan["total"]["SL-5 explain"] == 3 and plan["repeat"] == 3
        #: слот №3 идёт вне `llm_calls`, поэтому в лимит хода считаются только чат-слоты
        assert plan["chat_calls_per_run"] == 5 and plan["llm_calls_limit"] == 6
        assert plan["stopped_at"] is None and plan["outcome"] == "plan_ready"

    def test_the_plan_stops_at_a_question_it_has_no_answer_for(self, capsys,
                                                              monkeypatch) -> None:
        """Без флага вопрос остаётся вопросом: план обрывается и говорит, где именно.

        Доращивать его выдуманным ответом значило бы посчитать ход, которого на живом прогоне
        не будет, — и недооценить ровно те слоты, что идут после вопроса.
        """

        monkeypatch.delenv("RERANK_MODEL", raising=False)
        code = rp.main(["--provider", "mock", "--rerank-only", "--dry-run", "--json"])
        plan = json.loads(capsys.readouterr().out)["plan"]
        assert code == 0
        assert plan["stopped_at"] == "origin"
        assert plan["per_run"]["SL-4 finalists"] == 0 and plan["outcome"] is None

    def test_a_missing_key_stops_the_run_before_the_provider_is_built(self, capsys,
                                                                      monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "")
        monkeypatch.setenv("AITUNNEL_API_KEY", "")
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        code = rp.main(["--provider", "aitunnel", "--dry-run"])
        captured = capsys.readouterr()
        assert code == 2 and captured.out == ""
        assert "нет LLM_API_KEY" in captured.err

    def test_an_empty_model_name_is_named_per_slot(self, capsys, monkeypatch) -> None:
        """`model=""` — это не «не заплатим»: шлюз ответит ошибкой посреди хода.

        Сообщением по слоту, а не одним «провайдер не собрался»: `LLM_MODEL_PARSE` и
        `RERANK_MODEL` переопределяются независимо, и чинить надо ту переменную, которая пуста.
        """

        monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-real-one")
        for name in ("LLM_MODEL", "LLM_MODEL_PARSE", "LLM_MODEL_QUESTIONS",
                     "LLM_MODEL_FINALISTS", "LLM_MODEL_EXPLAIN", "LLM_MODEL_DIFF"):
            monkeypatch.setenv(name, "")
        monkeypatch.delenv("RERANK_MODEL", raising=False)
        code = rp.main(["--provider", "aitunnel", "--dry-run"])
        captured = capsys.readouterr()
        assert code == 2
        assert "не задана модель слота SL-1 parse: LLM_MODEL_PARSE" in captured.err
        assert "не задана модель слота SL-5 explain" in captured.err

    def test_preflight_reports_the_key_without_ever_printing_it(self, capsys,
                                                               monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-aitunnel-0123456789")
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        rp.main(["--provider", "mock", "--rerank-only", "--dry-run"])
        out = capsys.readouterr().out
        assert "sk-aitunnel-0123456789" not in out
        assert "ключ: задан, 22 символов" in out

    def test_the_hybrid_dry_run_is_free_and_says_so(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-aitunnel-0123456789")
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        stub = StubReranker()
        monkeypatch.setattr(Reranker, "from_config", classmethod(lambda cls: stub))
        code = rp.main(["--provider", "mock", "--rerank-only", "--dry-run",
                        "--answer", "origin=Москва", "--answer", "dates.start=с 14 по 21"])
        out = capsys.readouterr().out
        assert code == 0
        assert "SL-3 relevance        1      1  /rerank — платно" in out
        assert "чат — моки, бесплатно" in out

    def test_sl3_metrics_say_how_much_it_ranked_and_whether_the_order_changed(self, capsys,
                                                                             monkeypatch) -> None:
        """Приёмка слота №3 — порядок отелей и время обращения: оба числа в `--json`.

        `notes` для таблицы, а не для `jq`: «3 отеля» строкой не сравнивается между прогонами,
        и «проранжировали, но порядок тот же» неотличим от «проранжировали и переставили».
        """

        monkeypatch.setenv("LLM_API_KEY", "sk-aitunnel-0123456789")
        monkeypatch.setenv("RERANK_MODEL", "qwen3-reranker-8b")
        stub = StubReranker()
        monkeypatch.setattr(Reranker, "from_config", classmethod(lambda cls: stub))
        code = rp.main(["--provider", "mock", "--rerank-only", "--json",
                        "--answer", "origin=Москва", "--answer", "dates.start=с 14 по 21"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        slot3 = next(row for row in payload["slots"] if row["slot"] == "SL-3 relevance")
        assert slot3["calls"] == 1
        #: рядом с `calls` лежит число прогонов: «вызовов 2» при `--repeat 9` — это дыра в
        #: замере, а не маленькая латентность, и различать их надо по одному полю
        assert slot3["runs"] == 1
        assert slot3["docs"] == [5] and slot3["reordered"] == [True]
        #: у чат-слотов измерять нечего — пустые списки, а не ноль, которого не было
        slot1 = next(row for row in payload["slots"] if row["slot"] == "SL-1 parse")
        assert slot1["docs"] == [] and slot1["reordered"] == []

    def test_a_slot_that_fired_on_only_some_runs_is_called_out(self, capsys) -> None:
        """Пропущенный ходов слот таблицей не видно — он выглядит как маленькое число.

        Живой прогон 2026-10-28: `/rerank` вызван 2 раза из 9, и в столбце «вызовов» это
        просто двойка. Пока это не сказано строкой, замер reading-я выглядит «модель работала
        редко», а не «мы её не включали».
        """

        measured = rp.MeasuredSlots(rp.ChatMockRerankLive(cast(Reranker, StubReranker())))
        measured.soft_relevance("море", {"AYT-1": "a", "AYT-2": "b"})
        rp.print_slots(measured, 5)
        out = capsys.readouterr().out
        assert "SL-3 relevance: вызван 1 раз из 5 прогонов" in out

    def test_the_slots_table_prints_how_much_the_reranker_moved(self, capsys) -> None:
        """Таблица слотов — про качество порядка, а не только про время одного обращения."""

        measured = rp.MeasuredSlots(rp.ChatMockRerankLive(cast(Reranker, StubReranker())))
        measured.soft_relevance("море", {"AYT-1": "a", "AYT-2": "b", "AYT-3": "c"})
        measured.soft_relevance("море", {"AYT-1": "a"})
        rp.print_slots(measured)
        out = capsys.readouterr().out
        assert "отелей на вызов 1…3" in out
        assert "порядок источника изменён в 1 из 2" in out
