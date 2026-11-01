"""Деньги: только Decimal, копеечный масштаб, один источник итоговой суммы."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.contracts.enums import BudgetScope, CostScope, DataStatus, PriceKind

_CENTS = Decimal("0.01")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
#: Знак валюты для человекочитаемой строки: только у тех валют, чей знак мы не выдумываем.
#: Всё остальное остаётся ISO-кодом (`as_ru`), потому что «$» за USD без курса — та же
#: подмена, что и температура, которой в данных нет.
_SYMBOLS = {"RUB": "₽"}


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal amount: {value!r}") from exc
    raise ValueError("Money amount must be Decimal, int or str — binary fractions are forbidden")


class Money(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal
    currency: str

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float(cls, value: object) -> Decimal:
        return _to_decimal(value)

    @field_validator("currency")
    @classmethod
    def _iso_code(cls, value: str) -> str:
        if not _CURRENCY_RE.match(value):
            raise ValueError(f"currency must be an ISO-4217 code, got {value!r}")
        return value

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise TypeError(f"cannot add {self.currency} and {other.currency}: convert via a rated source first")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise TypeError(f"cannot subtract {self.currency} from {other.currency}")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def times(self, factor: int) -> Money:
        if factor <= 0:
            raise ValueError("factor must be positive")
        return Money(amount=self.amount * factor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._comparable(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._comparable(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._comparable(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._comparable(other)
        return self.amount >= other.amount

    def _comparable(self, other: object) -> None:
        if not isinstance(other, Money):
            raise TypeError("Money compares only with Money")
        if self.currency != other.currency:
            raise TypeError(f"cannot compare {self.currency} with {other.currency}")

    def rounded(self) -> Money:
        return Money(amount=self.amount.quantize(_CENTS, rounding=ROUND_HALF_UP), currency=self.currency)

    def as_display(self) -> str:
        """Машиночитаемая сумма: «150000 RUB». Её отдаём источникам как аргумент и в лог."""

        quantized = self.rounded().amount
        if quantized == quantized.to_integral_value():
            return f"{int(quantized)} {self.currency}"
        return f"{quantized} {self.currency}"

    def as_ru(self) -> str:
        """Сумма для человека: «150 000 ₽», при дробной — «1 234.50 USD».

        Отличается от `as_display` ровно видом: разряды через пробел и знак валюты там, где
        он у нас задан. Численное значение то же, поэтому `unverified_numbers` сверяет обе
        формы одинаково — сравнение идёт по цифрам без пробелов. ISO-код остаётся для всего,
        чего мы не решаемся подписывать знаком: «1 200 USD» честнее, чем выдуманный «$».
        """

        quantized = self.rounded().amount
        grouped = (f"{int(quantized):,}" if quantized == quantized.to_integral_value()
                   else f"{quantized:,}")
        return f"{grouped.replace(',', ' ')} {_SYMBOLS.get(self.currency, self.currency)}"


class PriceLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    line_id: str
    kind: PriceKind
    amount: Money
    scope: CostScope
    included_in_budget: bool
    source_id: str
    fetched_at: datetime
    data_status: DataStatus
    description: str | None = None


class BudgetLedger(BaseModel):
    """Единственное место, откуда берутся итоговые числа. `total` — функция от линий,
    поэтому разойтись с ними он не может."""

    model_config = ConfigDict(extra="forbid")

    lines: list[PriceLine] = Field(default_factory=list)
    budget: Money
    scope: BudgetScope
    #: Для ``per_person`` лимит и остаток считаются от всей группы, а не от одной персоны.
    party_size: int = Field(default=1, ge=1)

    @property
    def effective_budget(self) -> Money:
        """Фактический лимит поездки с учётом scope."""

        return self.budget.times(self.party_size) if self.scope == BudgetScope.PER_PERSON else self.budget

    @property
    def total(self) -> Money:
        ids = [line.line_id for line in self.lines if line.included_in_budget]
        duplicated = {line_id for line_id in ids if ids.count(line_id) > 1}
        if duplicated:
            raise ValueError(f"duplicate PriceLine ids in budget: {sorted(duplicated)}")
        total = Decimal(0)
        for line in self.lines:
            if line.included_in_budget:
                if line.amount.currency != self.budget.currency:
                    raise TypeError(
                        f"line {line.line_id} is {line.amount.currency} but budget is {self.budget.currency}"
                    )
                total += line.amount.amount
        return Money(amount=total, currency=self.budget.currency).rounded()

    @property
    def excluded_total(self) -> Money:
        total = Decimal(0)
        for line in self.lines:
            if not line.included_in_budget:
                if line.amount.currency != self.budget.currency:
                    raise TypeError("excluded lines must share the budget currency")
                total += line.amount.amount
        return Money(amount=total, currency=self.budget.currency).rounded()

    @property
    def breakdown(self) -> dict[PriceKind, Money]:
        acc: dict[PriceKind, Decimal] = {}
        for line in self.lines:
            if line.included_in_budget and line.amount.currency == self.budget.currency:
                acc[line.kind] = acc.get(line.kind, Decimal(0)) + line.amount.amount
        return {kind: Money(amount=value, currency=self.budget.currency) for kind, value in acc.items()}

    @property
    def over_budget(self) -> Money | None:
        """Сумма превышения над бюджетом; None — если бюджет не нарушен."""

        total = self.total
        return (total - self.effective_budget) if total > self.effective_budget else None

    @property
    def remaining(self) -> Money:
        return self.effective_budget - self.total
