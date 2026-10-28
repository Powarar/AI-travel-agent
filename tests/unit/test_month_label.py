"""Месяц по-русски: `2026-11` — ключ источника, а не строка для человека.

Один словарь на вопрос, карточку и объяснение (CONTRACTS.md, «Что видит человек»). Здесь
ровно он: что читается числом и в какой форме, а что означает молчание.
"""

from __future__ import annotations

import pytest

from shared.contracts.months import MONTH_NAMES, month_label


class TestMonthLabel:
    @pytest.mark.parametrize("number,name", list(enumerate(MONTH_NAMES, start=1)))
    def test_all_twelve_in_the_prepositional_case(self, number: int, name: str) -> None:
        assert month_label(number) == name

    def test_iso_key_and_bare_number_agree(self) -> None:
        #: `dates.month` носит вид «ГГГГ-ММ», аргумент источника — просто номер; обе формы
        #: обязаны давать ту же строку, иначе вопрос и карточка разошлись бы на одном месяце
        assert month_label("2026-11") == month_label(11) == month_label("11") == "ноябре"

    def test_full_date_reads_the_month_not_the_day(self) -> None:
        #: подсказка «например с 14 по 21» приходит вместе с датой; взять из неё «05» и
        #: показать «мае» было бы неверной подписью, поэтому год узнаётся по четырём цифрам
        assert month_label("2026-11-05") == "ноябре"

    def test_space_after_the_prefix_survives(self) -> None:
        assert month_label(" 2026-3 ") == "марте"

    @pytest.mark.parametrize("value", [None, "", "   ", "ноябрь", "11-05", "26-11", "abc", 0, 13, -1])
    def test_without_a_month_number_the_answer_is_silence(self, value: object) -> None:
        #: молчание, а не «0-м месяце» и не «январе» к заглушке: отсутствие числа — `unknown`;
        #: «11-05» тоже мимо — у даты без четырёхзначного года месяц угадывать нечем
        assert month_label(value) == ""
