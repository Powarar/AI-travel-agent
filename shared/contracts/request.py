"""TripRequest: каждое поле несёт метаданные, а не только значение (agent/CONTRACTS.md)."""

from __future__ import annotations

from datetime import date
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.contracts.enums import (
    BudgetInclude,
    BudgetScope,
    ConstraintKind,
    DirectionMode,
    FieldStatus,
    Theme,
    TransportMode,
)
from shared.contracts.money import Money

T = TypeVar("T")


class FieldValue(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    kind: ConstraintKind
    status: FieldStatus
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_confirmation: bool = False

    @model_validator(mode="after")
    def _check_source(self) -> FieldValue[T]:
        if self.status in (FieldStatus.KNOWN, FieldStatus.INFERRED, FieldStatus.DEFAULTED) and not self.source:
            raise ValueError(f"status={self.status.value} requires an explicit source")
        if self.status == FieldStatus.UNKNOWN and self.source not in (None, "user", "default"):
            raise ValueError("unknown field must not carry a fabricated source")
        if self.status == FieldStatus.UNKNOWN and self.value is not None:
            raise ValueError("unknown field must not carry a value")
        return self

    @classmethod
    def known(cls, value: T, *, kind: ConstraintKind, source: str = "user") -> FieldValue[T]:
        return cls(value=value, kind=kind, status=FieldStatus.KNOWN, source=source, confidence=1.0)

    @classmethod
    def inferred(cls, value: T, *, kind: ConstraintKind, source: str, confidence: float) -> FieldValue[T]:
        return cls(
            value=value,
            kind=kind,
            status=FieldStatus.INFERRED,
            source=source,
            confidence=confidence,
            needs_confirmation=True,
        )

    @classmethod
    def defaulted(cls, value: T, *, kind: ConstraintKind, source: str = "default") -> FieldValue[T]:
        return cls(value=value, kind=kind, status=FieldStatus.DEFAULTED, source=source)

    @classmethod
    def unknown(cls, *, kind: ConstraintKind) -> FieldValue[T]:
        return cls(value=None, kind=kind, status=FieldStatus.UNKNOWN)

    @property
    def is_unknown(self) -> bool:
        return self.status == FieldStatus.UNKNOWN or self.value is None


class Direction(BaseModel):
    mode: FieldValue[DirectionMode] = FieldValue.unknown(kind=ConstraintKind.HARD)
    explicit: FieldValue[str] = FieldValue.unknown(kind=ConstraintKind.HARD)
    excluded: FieldValue[list[str]] = FieldValue(
        value=[], kind=ConstraintKind.HARD, status=FieldStatus.DEFAULTED, source="default"
    )


class Dates(BaseModel):
    start: FieldValue[date] = FieldValue.unknown(kind=ConstraintKind.HARD)
    end: FieldValue[date] = FieldValue.unknown(kind=ConstraintKind.HARD)
    flexibility_days: FieldValue[int] = FieldValue.unknown(kind=ConstraintKind.SOFT)

    @field_validator("flexibility_days")
    @classmethod
    def _bounded(cls, field: FieldValue[int]) -> FieldValue[int]:
        if field.value is not None and not 0 <= field.value <= 7:
            raise ValueError("flexibility_days must be within 0..7")
        return field


class Party(BaseModel):
    adults: FieldValue[int] = FieldValue.unknown(kind=ConstraintKind.HARD)
    children: FieldValue[list[int]] = FieldValue.unknown(kind=ConstraintKind.HARD)
    pets: FieldValue[bool] = FieldValue.unknown(kind=ConstraintKind.HARD)


class Budget(BaseModel):
    amount: FieldValue[Money] = FieldValue.unknown(kind=ConstraintKind.HARD)
    currency: FieldValue[str] = FieldValue.defaulted("RUB", kind=ConstraintKind.HARD)
    scope: FieldValue[BudgetScope] = FieldValue.unknown(kind=ConstraintKind.HARD)
    includes: FieldValue[list[BudgetInclude]] = FieldValue.unknown(kind=ConstraintKind.HARD)


class Transport(BaseModel):
    modes: FieldValue[list[TransportMode]] = FieldValue.unknown(kind=ConstraintKind.SOFT)
    max_hours_direct: FieldValue[float] = FieldValue.unknown(kind=ConstraintKind.HARD)
    prefer_direct: FieldValue[bool] = FieldValue.unknown(kind=ConstraintKind.SOFT)
    overnight_ok: FieldValue[bool] = FieldValue.unknown(kind=ConstraintKind.SOFT)


class Lodging(BaseModel):
    rooms: FieldValue[int] = FieldValue.unknown(kind=ConstraintKind.HARD)
    stars_min: FieldValue[int] = FieldValue.unknown(kind=ConstraintKind.SOFT)
    budget_per_night_max: FieldValue[Money] = FieldValue.unknown(kind=ConstraintKind.SOFT)
    must: FieldValue[list[str]] = FieldValue(
        value=[], kind=ConstraintKind.HARD, status=FieldStatus.DEFAULTED, source="default"
    )


class Identity(BaseModel):
    """Только для бинарного флага `visa_required_for`; не для рекомендаций."""

    citizenship: FieldValue[str] = FieldValue.unknown(kind=ConstraintKind.HARD)
    residence: FieldValue[str] = FieldValue.unknown(kind=ConstraintKind.HARD)


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: FieldValue[str] = FieldValue.unknown(kind=ConstraintKind.HARD)
    direction: Direction = Direction()
    dates: Dates = Dates()
    nights: FieldValue[int] = FieldValue.unknown(kind=ConstraintKind.HARD)
    party: Party = Party()
    budget: Budget = Budget()
    transport: Transport = Transport()
    lodging: Lodging = Lodging()
    themes: FieldValue[list[Theme]] = FieldValue.unknown(kind=ConstraintKind.SOFT)
    soft_preferences: list[str] = Field(default_factory=list)
    identity: Identity = Identity()

    @property
    def pref_text(self) -> str:
        """Строит код, не модель: `" ".join(soft values)` (ARCHITECTURE.md §5)."""

        return " ".join(self.soft_preferences)

    def hard_known_fields(self) -> dict[str, FieldValue[object]]:
        result: dict[str, FieldValue[object]] = {}
        for name, field in self.iter_fields():
            if isinstance(field, FieldValue) and field.kind == ConstraintKind.HARD and not field.is_unknown:
                result[name] = field
        return result

    _SECTIONS: tuple[tuple[str, str], ...] = (
        ("direction", "direction"),
        ("dates", "dates"),
        ("party", "party"),
        ("transport", "transport"),
        ("lodging", "lodging"),
        ("identity", "identity"),
    )

    def iter_fields(self) -> list[tuple[str, object]]:
        """Имена путей — точно как в таблице CONTRACTS.md (`direction.mode`, `party.pets`);
        на них индексируется реестр T-04."""

        flat: list[tuple[str, object]] = [("origin", self.origin), ("nights", self.nights)]
        for prefix, attr in self._SECTIONS:
            section = getattr(self, attr)
            for name, field in section:
                flat.append((f"{prefix}.{name}", field))
        flat.append(("budget.amount", self.budget.amount))
        flat.append(("budget.currency", self.budget.currency))
        flat.append(("budget.scope", self.budget.scope))
        flat.append(("budget.includes", self.budget.includes))
        flat.append(("themes", self.themes))
        flat.append(("soft_preferences", self.soft_preferences))
        return flat

    def field_value(self, path: str) -> object | None:
        for name, field in self.iter_fields():
            if name == path:
                return field
        return None
