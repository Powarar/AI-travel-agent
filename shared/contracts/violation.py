"""Violation — структура, а не текст: из неё репейер берёт вход, фронт рисует «что не так»."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.enums import Severity, ViolationCode


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ViolationCode
    severity: Severity
    affected_component: str
    field_path: str | None = None
    expected: str | None = None
    actual: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)
    repairable: bool
    suggested_actions: list[str] = Field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.severity == Severity.BLOCKING

    def signature(self) -> str:
        """Дедуп действий repair-петли: (code, component) — подпись из T-08."""

        return f"{self.code.value}:{self.affected_component}"
