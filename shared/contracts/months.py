"""Названия месяцев для строк пользователю — один словарь на вопрос, карточку и слоты.

Числовой месяц — это ключ источника (`2026-11` в `data/seeds/climate.json`, `11` в
`dates.month`), а не фраза человека. Пока каждый текст подставлял его как число, пользователь
видел «Назовите вилку в 11-м месяце» и «воздух +30.8 °C в 2026-11»: оба читаются как
заглушка, ни один не звучит как русская строка.

`llm/schemas.month_of` делает обратное (слово → номер) и обязана сходиться с этим списком по
набору месяцев: если здесь появится «сентябрь», а там не появится, разборщик потеряет дату,
которую карточка уже показывает.
"""

from __future__ import annotations

#: предложный падеж — та форма, в которой месяц звучит после предлога «в»
MONTH_NAMES: tuple[str, ...] = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
)


def month_label(value: object) -> str:
    """Номер месяца по-русски: `11`, `"11"`, `"2026-11"`, `"2026-11-05"` → «ноябре»; мимо → «»."""

    number: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str):
        #: `dates.month` носит вид «ГГГГ-ММ», аргумент источника — просто номер, а полная дата
        #: («2026-11-05») — тот же месяц, а не «05»: год в первой группе из четырёх цифр
        parts = value.strip().split("-")
        head = parts[0]
        if len(parts) == 1:
            candidate = head
        elif len(parts) in (2, 3) and len(head) == 4 and head.isdigit():
            candidate = parts[1]
        else:
            candidate = ""
        if candidate.isdigit():
            number = int(candidate)
    if number is None or not 1 <= number <= 12:
        return ""
    return MONTH_NAMES[number - 1]
