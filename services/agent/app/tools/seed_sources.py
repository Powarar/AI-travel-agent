"""Источник поверх каталога `data/seeds/*.json` (T-05): те же шесть сигнатур, что у фикстур.

До этого контур мерился только на фикстурах золотого пути, и «источник нашёл направление»
означало «в фикстуре лежит строка с этим кодом». Здесь тот же интерфейс
(`Mapping[str, object] → SearchResult`) читает реальный сид, поэтому отказа «не нашли»
достаточно, чтобы прогон честно упёрся в `infeasible`, — а `SearchResult.meta` обязывает
источник посчитать, сколько строк каталога он рассмотрел и сколько отсёк.

Границы, которые модуль не переступает:

* ни одного числа «на глаз»: деньги, минуты и температуры — из строк сида. Чего в строке
  нет (морской температуры у климата, вместимости у номера), то уходит `None`, а не оценкой
  и не нулём — CONTRACTS.md, «Климат: месяц поездки и отсутствующее число»;
* `--sources fixture` остаётся значением по умолчанию в прогоне: ассерты T-05 сходятся с
  фикстурой до копейки, и подменять её здесь значило бы мерить свой фильтр вместо чужого;
* обратных сегментов в сиде нет (все 3788 строк — рейсы «туда»), поэтому обратный рейс —
  зеркало прямого: тот же маршрут в обратную сторону, те же `duration_min`, вылет на дату
  конца вилки в то же время суток, что и вылет прямого. Допущение названо в CONTRACTS.md;
* пустой аргумент означает «пользователь не сказал», и фильтр по такому полю не применяется:
  молчание не имеет права превращать выдачу в ноль.

Аргументы приходят те, что объявлены в `Tool.inputs` (`registry.py`), в plain-виде рантайма
(`runtime._plain`): даты — ISO-строки, `Money` — dict из `model_dump(mode="json")`,
неизвестное поле — пустая строка, перечисления — их значения.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from shared.contracts.enums import CostScope, DataStatus, PriceKind
from shared.contracts.money import Money, PriceLine
from services.agent.app.orchestration.nodes import (
    ClimateFact,
    DestinationFact,
    HotelOffer,
    SearchResult,
    TransferOffer,
    TransportOffer,
)
from services.agent.app.tools.registry import REGISTRY, args_hash
from services.agent.app.validation.coherence import named_matches
from services.agent.app.validation.inputs import HotelSpecs, StayNight, TransferLeg, TransportLeg

SEEDS_DIR = Path(__file__).resolve().parents[4] / "data" / "seeds"
UTC = timezone.utc

logger = logging.getLogger(__name__)

#: файлы, без которых каталога нет: ровно тот список, что читает `catalog()`
CATALOG_FILES: tuple[str, ...] = (
    "sources.json", "destinations.json", "climate.json", "transport_legs.json",
    "hotels.json", "stay_rates.json", "transfers.json", "gateways.json", "airports.json",
)

#: Балл подбора — нормализованная температура месяца поездки. Границы 0.05–0.95 не косметика:
#: слот №4 сортирует по `score`, а неоценённый кандидат читает 0.0, поэтому оценённый обязан
#: остаться выше (то же правило, что в `nodes._hotel_order`).
_SCORE_FLOOR = Decimal("0.05")
_SCORE_TOP = Decimal("0.95")

#: Русские имена удобств сида: `lodging.must` пользователь называет словом («бассейн»), а
#: `hotels.json` хранит slug («pool»). Словарь назван, а не угадан по схожести букв; незнакомое
#: слово не отсеивает ничего, потому что права «догадаться» у источника нет.
_AMENITY_RU: dict[str, str] = {
    "бассейн": "pool",
    "пляж": "beach_first_line",
    "берег": "beach_first_line",
    "завтрак": "breakfast",
    "спа": "spa",
    "детский": "kids_club",
    "парковка": "parking",
    "интернет": "wifi",
    "зал": "gym",
    "фитнес": "gym",
    "ресторан": "restaurant",
    "трансфер": "airport_shuttle",
    "шаттл": "airport_shuttle",
    "кондиционер": "air_conditioning",
    "стирка": "laundry",
}

#: «St. Petersburg» в `airports.json` — сокращение, транслитерацией «петербург» с ним не
#: совпадает. Разворачиваем на стороне источника, правило локальное и названное.
_ABBREVIATION_RE = re.compile(r"^st\.?\s+", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\D")


@dataclass(frozen=True)
class _Catalog:
    """Сид в памяти один раз: индексы вместо повторных сканов по 44 тысячам строк."""

    destinations: list[dict[str, Any]]
    destination_tz: dict[str, str]
    climate: dict[tuple[str, int], dict[str, Any]]
    legs: list[dict[str, Any]]
    hotels: list[dict[str, Any]]
    rates: dict[tuple[str, str], dict[str, Any]]
    transfers: list[dict[str, Any]]
    gateways: dict[str, dict[str, Any]]
    airports: list[dict[str, Any]]
    fetched: dict[str, datetime]

    @property
    def departure_iata(self) -> set[str]:
        return {str(leg["dep_iata"]) for leg in self.legs}

    def min_flight_minutes(self) -> dict[str, Decimal]:
        """Самый короткий перелёт к направлению по всем аэропортам выезда, которые есть в сиде."""

        best: dict[str, Decimal] = {}
        for leg in self.legs:
            code = str(leg["dest_code"])
            minutes = Decimal(leg["duration_min"])
            if code not in best or minutes < best[code]:
                best[code] = minutes
        return best


def _read(name: str) -> Any:
    return json.loads((SEEDS_DIR / name).read_text(encoding="utf-8"))


class CatalogMissing(FileNotFoundError):
    """`data/seeds` не собран: источник `seed` нечем читать.

    Свой тип вместо голого `FileNotFoundError` — затем, чтобы `run_pipeline.py` отличал
    «не собрал каталог» от «сломался файл по дороге» и отвечал оператору строкой-инструкцией,
    а не трекбеком из середины хода.
    """


def require_catalog() -> None:
    """Каталог на месте — иначе падать до первого запроса, а не посреди прогона.

    Без проверки `--sources seed` при отсутствующем `data/seeds` доходил до ответа слота №1
    и падал `FileNotFoundError` из первого инструмента: сеть уже была бы потрачена, а
    оператор видел бы трекбек вместо «соберите сид или возьмите фикстуры».
    """

    missing = [name for name in CATALOG_FILES if not (SEEDS_DIR / name).is_file()]
    if missing:
        raise CatalogMissing(
            f"нет файлов каталога в {SEEDS_DIR}: {', '.join(missing)}; "
            "соберите сид (python3 data/generators/run_all.py) "
            "или запустите прогон без --sources seed")


@lru_cache(maxsize=1)
def catalog() -> _Catalog:
    """Загрузка сида; кэш — потому что файлы за прогон не меняются, а сканов будет шесть."""

    require_catalog()
    sources = cast("list[dict[str, Any]]", _read("sources.json"))
    destinations = cast("list[dict[str, Any]]", _read("destinations.json"))
    return _Catalog(
        destinations=destinations,
        #: зона направления — единственный честный запасной вариант, когда у строки рейса
        #: `arr_tz` пустой `\N` (68 сегментов сида); брать её с соседнего аэропорта или
        #: переводить стрелки на глаз — значит подменить число
        destination_tz={
            str(row["code"]): str(row.get("timezone") or "")
            for row in destinations
        },
        climate={
            (str(row["dest_code"]), int(row["month"])): row
            for row in cast("list[dict[str, Any]]", _read("climate.json"))
        },
        legs=cast("list[dict[str, Any]]", _read("transport_legs.json")),
        hotels=cast("list[dict[str, Any]]", _read("hotels.json")),
        rates={
            (str(row["hotel_id"]), str(row["date"])): row
            for row in cast("list[dict[str, Any]]", _read("stay_rates.json"))
        },
        transfers=cast("list[dict[str, Any]]", _read("transfers.json")),
        gateways=cast("dict[str, dict[str, Any]]", _read("gateways.json")),
        airports=cast("list[dict[str, Any]]", _read("airports.json")),
        fetched={
            str(row["source_id"]):
                datetime.fromisoformat(str(row["fetched_at"]).replace("Z", "+00:00"))
            for row in sources
        },
    )


# ------------------------------------------------------------------ аргументы


def _text(args: Mapping[str, object], key: str) -> str:
    value = args.get(key)
    if value is None or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return ""
    return str(value).strip()


def _items(args: Mapping[str, object], key: str) -> list[str]:
    value = args.get(key)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value]
    return []


def _number(args: Mapping[str, object], key: str) -> Decimal | None:
    """Число аргумента: пустая строка — «не сказано», а не ноль."""

    raw = _text(args, key)
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _money_value(args: Mapping[str, object], key: str) -> Money | None:
    """`Money` из plain-аргумента: рантайм отдаёт `model_dump(mode="json")`, то есть dict."""

    value = args.get(key)
    if isinstance(value, Money):
        return value
    if isinstance(value, Mapping):
        amount = value.get("amount")
        if amount in (None, ""):
            return None
        return Money(amount=Decimal(str(amount)), currency=str(value.get("currency") or "RUB"))
    raw = _text(args, key)
    return Money(amount=Decimal(raw), currency="RUB") if raw else None


def _flag(args: Mapping[str, object], key: str) -> bool | None:
    """Три состояния: `True`/`False` из запроса и `None`, когда пользователь молчит."""

    value = args.get(key)
    if isinstance(value, bool):
        return value
    raw = _text(args, key).lower()
    if raw in {"true", "yes", "1", "да"}:
        return True
    if raw in {"false", "no", "0", "нет"}:
        return False
    return None


def _count(args: Mapping[str, object], key: str) -> int:
    digits = _DIGITS_RE.sub("", _text(args, key))
    return int(digits) if digits else 0


def _day(args: Mapping[str, object], key: str) -> date | None:
    raw = _text(args, key)[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _codes(args: Mapping[str, object]) -> list[str]:
    """Коды направлений из аргумента; если их ещё нет — то же разрешение по названному слову.

    Инструменты перелётов и отелей запускаются и до ответа источника направлений (guard
    пропускает `direction.explicit`), и тогда «искать по всему каталогу» было бы неправдой:
    выдача на 1296 отелей при нуле направлений — это не ответ на запрос пользователя.
    """

    codes = [str(code) for code in _items(args, "destinations")]
    if codes:
        return codes
    named = _text(args, "direction.explicit")
    if not named:
        return []
    return [str(row["code"]) for row in catalog().destinations
            if named_matches(named, (str(row["code"]), str(row["name"]), str(row["country"])))]


def _month(args: Mapping[str, object]) -> int | None:
    """Месяц поездки из `dates.month` (`2026-11`): климат сида ключуется целым месяцем."""

    raw = _text(args, "dates.month").rpartition("-")[2]
    if raw.isdigit() and 1 <= int(raw) <= 12:
        return int(raw)
    return None


def _status(row: Mapping[str, Any]) -> DataStatus:
    try:
        return DataStatus(str(row.get("data_status", "unknown")))
    except ValueError:
        return DataStatus.UNKNOWN


def _fetched(source_id: str) -> datetime:
    known = catalog().fetched
    #: строка сида без метки забрала не позже остальных — детерминированная граница вместо
    #: `now`, который сделал бы повторный прогон непохожим на первый
    return known.get(source_id) or min(known.values())


def _price(line_id: str, kind: PriceKind, amount: object, currency: object,
           scope: CostScope, source_id: str, status: DataStatus) -> PriceLine:
    """Строка цены из целого числа сида: `Decimal` собирается из `str`, бинарных долей нет."""

    return PriceLine(
        line_id=line_id,
        kind=kind,
        amount=Money(amount=Decimal(str(amount)), currency=str(currency or "RUB")),
        scope=scope,
        included_in_budget=True,
        source_id=source_id,
        fetched_at=_fetched(source_id),
        data_status=status,
    )


def _hours(minutes: Decimal) -> float:
    """Часы в дороге: поле контракта — `float`, и на деньги оно не влияет."""

    return float((minutes / Decimal(60)).quantize(Decimal("0.1")))


#: в `airports.json` и `transport_legs.json` пустое значение зоны записано как `\N` (маркер
#: NULL из дампа), а не как пустая строка
_NULL_TZ = "\\N"


def _usable_tz(name: str) -> ZoneInfo | None:
    r"""Зона из строки сида, если она существует; `None` — если это `\N` или имя из выдумки."""

    if not name or name == _NULL_TZ:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def _tz(name: str, backup: str = "") -> timezone | ZoneInfo:
    r"""Зона строки рейса; при `\N` — зона самого направления, и только без неё — UTC.

    68 сегментов Иссык-Куля приходят с `\N`, а метка часа улетает наружу в `ref.arr_at`:
    «прилёт 04:47+00:00» там, где пояс `+06:00`, — ложь в ответе. Запасная зона берётся из
    той же таблицы (`destinations.json`), а не из головы. Несовместимость самого `arr_at`
    с поясами — дыра сида, названная в CONTRACTS.md, и здесь мы её молча не чиним.
    """

    zone = _usable_tz(name) or _usable_tz(backup)
    if zone is None:
        logger.warning("seed row has no usable timezone (tz=%r backup=%r); using UTC",
                       name, backup)
        return UTC
    return zone


def _aware(iso: str, tz_name: str, backup: str = "") -> datetime:
    """Местное время сида с его же зоной; без зоны — зона направления, а не пояс машины."""

    return datetime.fromisoformat(iso).replace(tzinfo=_tz(tz_name, backup))


def _local_day(iso: str) -> date | None:
    """Дата из стенного времени строки сида (`2026-11-02T04:47`); кривая строка — `None`.

    Пояс к этой дате пришивать незачем: сутки в столбце записаны местными часами, и
    `date()` от зоны не меняется — зато `None` на непарсящейся строке отсеивает рейс,
    а не пропускает его вслепую.
    """

    try:
        return date.fromisoformat(iso[:10])
    except ValueError:
        return None


def _destination_tz(code: str) -> str:
    r"""Зона направления из `destinations.json` — единственный запасной вариант для `\N`."""

    return catalog().destination_tz.get(code, "")


def _expand(text: str) -> str:
    return _ABBREVIATION_RE.sub("saint ", text)


def _origin_airports(origin: str) -> set[str]:
    """Аэропорты выезда по названию пользователя: муниципалитет сида ∩ реально летающие."""

    if not origin:
        return set()
    flying = catalog().departure_iata
    return {
        str(row["iata"]) for row in catalog().airports
        if str(row["iata"]) in flying
        and named_matches(origin, (_expand(str(row.get("municipality") or "")),))
    }


# ------------------------------------------------------------------ инструменты


def suggest_destinations(args: Mapping[str, object]) -> SearchResult:
    """Каталог направлений → кандидаты; отсекаем по названному слову, темам и часам в дороге.

    `direction.explicit` фильтруется тем же разбором слов, что и проверка плана
    (`coherence.named_matches`): иначе источник отдал бы один набор, валидатор сверил бы
    другой, и расхождение заметил бы пользователь, а не контур.
    """

    rows = catalog().destinations
    kept = list(rows)
    named = _text(args, "direction.explicit")
    max_hours = _number(args, "transport.max_hours_direct")

    def identity(row: dict[str, Any]) -> tuple[str, str, str]:
        return (str(row["code"]), str(row["name"]), str(row["country"]))

    if named:
        kept = [row for row in kept if named_matches(named, identity(row))]
    for word in _items(args, "direction.excluded"):
        kept = [row for row in kept if not named_matches(word, identity(row))]
    themes = _items(args, "themes")
    if themes:
        kept = [row for row in kept if str(row.get("theme", "")) in themes]

    minutes = catalog().min_flight_minutes()
    if max_hours is not None:
        limit = max_hours * Decimal(60)
        kept = [row for row in kept
                if minutes.get(str(row["code"])) is not None
                and minutes[str(row["code"])] <= limit]

    month = _month(args)
    warm: dict[str, Decimal] = {}
    if month is not None:
        for row in kept:
            climate = catalog().climate.get((str(row["code"]), month))
            if climate is not None:
                warm[str(row["code"])] = Decimal(str(climate["t_max"]))
    scores = _normalize(warm)
    #: `score` — детерминированный приоритет источника (монотонная функция от `t_max` месяца
    #: поездки), а не «модель сочла направление релевантным»: мягкое предпочтение пользователя
    #: выражают слот №3 (отели) и слот №4 (направления), и их число кладётся в
    #: `ref["relevance"]`, не сюда (CONTRACTS.md, «Мягкая релевантность (слот №3)»)

    facts = [
        DestinationFact(
            code=str(row["code"]),
            name=str(row["name"]),
            country=str(row["country"]),
            #: морской температуры в сиде климата нет ни у одного из 61 направлений — `None`,
            #: а не ноль и не «средняя по черноморскому побережью»
            sea_temp_c=None,
            flight_h=(f"{_hours(minutes[str(row['code'])])}"
                      if str(row["code"]) in minutes else ""),
            #: «цена от» считает ночь и состав, которых в аргументах подбора нет; вывести её
            #: из средней по каталогу — значит выдать выдумку за число источника
            est_total=None,
            source_id=str(row["source_id"]),
            score=float(scores.get(str(row["code"]), _SCORE_FLOOR)),
            data_status=_status(row),
        )
        for row in kept
    ]
    return SearchResult(
        destinations=facts,
        meta={
            "направлений в каталоге": str(len(rows)),
            "направлений в выдаче": str(len(facts)),
            "направлений названо": named or "(не названо)",
        },
    )


def _normalize(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Температура → балл по размаху этого же ответа: порядок, а не вердикт «тепло/холодно»."""

    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    span = high - low
    if span <= 0:
        return {code: _SCORE_TOP for code in values}
    return {
        code: _SCORE_FLOOR + (value - low) / span * (_SCORE_TOP - _SCORE_FLOOR)
        for code, value in values.items()
    }


def weather_climate(args: Mapping[str, object]) -> SearchResult:
    """Погода по паре `(dest_code, month)`: месяц поездки — часть ключа, а не «среднее за год».

    Названное направление без кодов в `destinations` (источник направлений ещё не отвечал)
    резолвится по тому же каталогу, что и подбор, — иначе карточка осталась бы без числа там,
    где пользователь назвал направление сам.
    """

    codes = _codes(args)
    month = _month(args)
    facts: list[ClimateFact] = []
    if month is not None:
        for code in codes:
            row = catalog().climate.get((code, month))
            if row is None:
                continue
            facts.append(ClimateFact(
                code=code,
                month=_text(args, "dates.month"),
                #: число воды в сиде отсутствует — `None`, и карточка промолчит про море
                sea_temp_c=None,
                day_temp_c=Decimal(str(row["t_max"])),
                source_id=str(row["source_id"]),
                data_status=_status(row),
            ))
    return SearchResult(
        climate=facts,
        meta={
            "строк климата в сиде": str(len(catalog().climate)),
            "строк климата в выдаче": str(len(facts)),
            "месяц поездки": _text(args, "dates.month") or "(не назван)",
        },
    )


def transport_search(args: Mapping[str, object]) -> SearchResult:
    """Перелёты туда из сида плюс зеркальный обратный сегмент.

    Фильтры — только по названному: аэропорт выезда, дата заезда, `prefer_direct`, отказ от
    ночных рейсов, режим. `transport.max_hours_direct` выдачу не режет: его проверяет сборка
    и отвечает числом, а не молчаливым «не нашли».
    """

    start = _day(args, "dates.start")
    end = _day(args, "dates.end")
    prefer_direct = _flag(args, "transport.prefer_direct")
    overnight_ok = _flag(args, "transport.overnight_ok")
    modes = _items(args, "transport.modes")

    legs = catalog().legs
    origin_iatas = _origin_airports(_text(args, "origin"))
    meta = {
        "сегментов в сиде": str(len(legs)),
        "аэропортов выезда нашли": str(len(origin_iatas)),
        "рейсов в выдаче": "0",
    }
    if not origin_iatas or not _codes(args):
        return SearchResult(meta=meta)
    if modes and "plane" not in modes:
        #: в сиде только самолёты: поезд или авто — это «режима нет в данных», а не «не нашли»
        return SearchResult(meta=dict(meta, **{"режимов в сиде": "plane"}))

    gateways = catalog().gateways
    offers: list[TransportOffer] = []
    for code in _codes(args):
        gateway = gateways.get(code)
        if gateway is None:
            continue
        iata = str(gateway["iata"])
        inbound = [
            leg for leg in legs
            if str(leg["dest_code"]) == code and str(leg["arr_iata"]) == iata
            and str(leg["dep_iata"]) in origin_iatas
        ]
        #: прилёт обязан быть в день заезда: рейс, приземляющийся на сутки позже, — это уже
        #: другая поездка, и ночей под неё в ставках сида не будет. День берётся из строки
        #: сида напрямую (`2026-11-02T04:47` → 2 ноября): дата стенных часов от пояса не
        #: зависит, а `_aware` на 68 строках с `\N` только шумел бы в лог
        if start is not None:
            inbound = [leg for leg in inbound
                       if _local_day(str(leg["arr_at"])) == start]
        if prefer_direct is True:
            inbound = [leg for leg in inbound if leg["direct"] is True]
        if overnight_ok is False:
            inbound = [leg for leg in inbound if leg["overnight"] is not True]
        for leg in inbound:
            passengers = max(1, _count(args, "party.adults") + len(_items(args, "party.children")))
            offers.append(_pair(leg, end, passengers=passengers))
    offers.sort(key=lambda offer: (offer.price_total, offer.arrival.leg_id))
    meta["рейсов в выдаче"] = str(len(offers))
    return SearchResult(transport=offers, meta=meta)


def _pair(leg: Mapping[str, Any], end: date | None, *, passengers: int = 1) -> TransportOffer:
    """Прямой сегмент из сида и обратный — его зеркало на дату конца вилки."""

    backup = _destination_tz(str(leg["dest_code"]))
    arr = _aware(str(leg["arr_at"]), str(leg["arr_tz"]), backup)
    dep = _aware(str(leg["dep_at"]), str(leg["dep_tz"]))
    minutes = Decimal(leg["duration_min"])
    currency = leg.get("currency")
    source_id = str(leg["source_id"])
    status = _status(leg)
    leg_id = str(leg["leg_id"])
    group_price = Decimal(str(leg["base_price_rub"])) * passengers

    arrival = TransportLeg(
        leg_id=leg_id,
        component="transport_in",
        dep_at=dep,
        arr_at=arr,
        duration_h=_hours(minutes),
        direct=bool(leg["direct"]),
        overnight=bool(leg["overnight"]),
        price=_price(f"transport:{leg_id}", PriceKind.TRANSPORT, group_price,
                     currency, CostScope.TOTAL, source_id, status),
    )
    #: обратного сегмента в сиде нет — зеркало: тот же борт, те же минуты, вылет в то же
    #: время суток на дату конца вилки; если даты нет, считаем неделю по прилёту
    back_date = end if end is not None else arr.date() + timedelta(days=7)
    back_dep = datetime.combine(back_date, dep.timetz()).replace(tzinfo=dep.tzinfo)
    back_arr = back_dep + timedelta(minutes=int(minutes))
    back_id = f"{leg_id}-ret"
    departure = TransportLeg(
        leg_id=back_id,
        component="transport_out",
        dep_at=back_dep,
        arr_at=back_arr,
        duration_h=_hours(minutes),
        direct=bool(leg["direct"]),
        #: ночным обратный рейс считает его же расписание: вылет и прилёт в разные сутки
        overnight=back_dep.date() != back_arr.date(),
        price=_price(f"transport:{back_id}", PriceKind.TRANSPORT, group_price,
                     currency, CostScope.TOTAL, source_id, status),
    )
    return TransportOffer(
        destination_code=str(leg["dest_code"]),
        arrival=arrival,
        departure=departure,
        data_status=status,
    )


def hotel_search(args: Mapping[str, object]) -> SearchResult:
    """Отели и ставки по датам ночёвки: отсекаем по питомцу, звёздам, удобствам и цене ночи.

    Вместимости у строк сида нет (`hotels.json` её не содержит), поэтому `lodging.rooms` и
    число гостей номер не проверяют — зафиксированный пробел данных, а не разрешение
    «подставить четвёрку».
    """

    codes = _codes(args)
    start = _day(args, "dates.start")
    nights = _count(args, "nights")
    if not nights:
        end = _day(args, "dates.end")
        nights = (end - start).days if start is not None and end is not None else 0
    pets = _flag(args, "party.pets")
    stars_min = _number(args, "lodging.stars_min")
    per_night_max = _money_value(args, "lodging.budget_per_night_max")
    must = _items(args, "lodging.must")

    rows = catalog().hotels
    considered = [row for row in rows if str(row["dest_code"]) in codes]
    kept: list[HotelOffer] = []
    for row in considered:
        if pets is True and row["pet_friendly"] is not True:
            continue
        if stars_min is not None and Decimal(int(row["stars"])) < stars_min:
            continue
        amenities = [str(amenity) for amenity in row["amenities"]]
        if not _has_amenities(amenities, must):
            continue
        stays = _stays(str(row["hotel_id"]), start, nights)
        if per_night_max is not None and any(
                stay.price.amount.amount > per_night_max.amount for stay in stays):
            continue
        kept.append(HotelOffer(
            destination_code=str(row["dest_code"]),
            name=str(row["name"]),
            stars=int(row["stars"]),
            specs=HotelSpecs(
                hotel_id=str(row["hotel_id"]),
                pet_friendly=bool(row["pet_friendly"]),
                check_in_deadline=str(row["check_in_deadline"]),
                capacity=None,
                min_nights=int(row["min_nights"]),
                source_id=str(row["source_id"]),
            ),
            stays=stays,
            amenities=amenities,
            #: источник не ранжирует отели: порядок задаёт цена (`_hotel_order`) и, если слот
            #: №3 работал, мягкая релевантность — балл здесь нейтральный, а не «оценка сида»
            score=0.0,
            data_status=_status(row),
        ))
    return SearchResult(
        hotels=kept,
        meta={
            "отелей в каталоге": str(len(rows)),
            "отелей на направление": str(len(considered)),
            "отелей в выдаче": str(len(kept)),
        },
    )


def _has_amenities(amenities: list[str], must: list[str]) -> bool:
    """`lodging.must` по удобствам: slug сида или названное русское слово из словаря."""

    wanted = {_AMENITY_RU.get(word, word) for word in (item.strip().lower() for item in must) if word}
    return wanted <= set(amenities)


def _stays(hotel_id: str, start: date | None, nights: int) -> list[StayNight]:
    """Ставки по датам ночёвки: чего в сиде нет, того в плане не бывает.

    Пропуск дня (вилка вышла за 2026-11-01…2026-12-07) остаётся пропуском: сборка отсяет
    отель по длине списка, и это честный `infeasible`, а не подставленная ставка.
    """

    if start is None or nights <= 0:
        return []
    stays: list[StayNight] = []
    for offset in range(nights):
        day = start + timedelta(days=offset)
        row = catalog().rates.get((hotel_id, day.isoformat()))
        if row is None:
            break
        source_id = str(row["source_id"])
        status = _status(row)
        stays.append(StayNight(
            date=day,
            price=_price(f"lodging:{hotel_id}:{day:%Y%m%d}", PriceKind.LODGING,
                         row["price_per_night"], row.get("currency"),
                         CostScope.PER_NIGHT, source_id, status),
            availability=bool(row["availability"]),
            min_nights=int(row["min_nights"]),
            source_id=source_id,
            fetched_at=_fetched(source_id),
            data_status=status,
        ))
    return stays


def transfer_search(args: Mapping[str, object]) -> SearchResult:
    """Трансферы по строкам отеля: в сиде одна строка `trf-<hotel_id>` на гостиницу.

    Поэтому `TransferOffer.hotel_id` заполняется — сборка обязана взять путь именно того
    отеля, который выбран, а не первого из списка.
    """

    codes = _codes(args)
    guests = _count(args, "party.adults") + len(_items(args, "party.children"))
    pets = _flag(args, "party.pets")

    rows = catalog().transfers
    kept: list[TransferOffer] = []
    for row in rows:
        if str(row["dest_code"]) not in codes:
            continue
        if guests and int(row["capacity"]) < guests:
            continue
        if pets is True and row["pet_ok"] is not True:
            continue
        kept.append(_transfer(row))
    kept.sort(key=lambda offer: offer.hotel_id)
    return SearchResult(
        transfers=kept,
        meta={
            "строк трансфера в сиде": str(len(rows)),
            "трансферов в выдаче": str(len(kept)),
        },
    )


def _transfer(row: Mapping[str, Any]) -> TransferOffer:
    """Один и тот же путь туда и обратно: сид даёт минуты и цену, направление неважно."""

    minutes = int(Decimal(str(row["duration_s"])) // 60)
    source_id = str(row["source_id"])
    status = _status(row)
    legs = [
        TransferLeg(
            transfer_id=f"{row['transfer_id']}-{kind}",
            kind=kind,
            minutes=minutes,
            price=_price(f"transfer:{row['transfer_id']}-{kind}", PriceKind.TRANSFER,
                         row["price_rub"], row.get("currency"),
                         CostScope.TOTAL, source_id, status),
            pet_ok=bool(row["pet_ok"]),
            capacity=int(row["capacity"]),
        )
        for kind in ("arrival", "departure")
    ]
    return TransferOffer(
        destination_code=str(row["dest_code"]),
        arrival=legs[0],
        departure=legs[1],
        hotel_id=str(row["hotel_id"]),
        data_status=status,
    )


def checkout_mock(args: Mapping[str, object]) -> SearchResult:
    """Заказ по содержимому корзины: тот же basket — тот же `order_id` (идемпотентность).

    Хеш берётся от корзины, а не от всех аргументов: `idempotency_key` рантайм по умолчанию
    берёт из `run_id` — случайного UUID, и вместе с ним `order_id` переставал быть
    воспроизводимым, а повторный прогон переставал давать тот же JSON («повтор с тем же
    ключом» коротит сам рантайм: `run.order_id` уже заполнен, второго вызова нет).
    """

    basket = args.get("basket")
    return SearchResult(
        order_id=f"ord-seed-{args_hash(REGISTRY['checkout_mock'], {'basket': basket})}")


#: шесть инструментов — те же имена, что в `REGISTRY`; прогон собирает из этого `Source`-map
SEED_SOURCES: dict[str, Callable[[Mapping[str, object]], SearchResult]] = {
    "suggest_destinations": suggest_destinations,
    "weather_climate": weather_climate,
    "transport_search": transport_search,
    "hotel_search": hotel_search,
    "transfer_search": transfer_search,
    "checkout_mock": checkout_mock,
}


def seed_sources() -> dict[str, Callable[[Mapping[str, object]], SearchResult]]:
    """Копия реестра источников: `scripts/run_pipeline.py --sources seed`.

    Проверка каталога здесь, а не только в `catalog()`: сборщик источников вызывается
    прогоном до маршрутинга моделей, поэтому «сида нет» становится видно до первого платного
    запроса, а не после ответа слота №1 посреди хода.
    """

    require_catalog()
    return dict(SEED_SOURCES)
