"""TripState — единственный источник истины (инвариант №1); версии не мутируются."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.enums import DataStatus, Outcome, RunStatus, Stage, ViolationCode
from shared.contracts.money import Money, PriceLine
from shared.contracts.request import TripRequest
from shared.contracts.violation import Violation


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    kind: str
    ref: dict[str, str] = Field(default_factory=dict)
    score: float | None = None
    data_status: DataStatus = DataStatus.UNKNOWN


class Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destinations: list[Candidate] = Field(default_factory=list)
    transport: list[Candidate] = Field(default_factory=list)
    hotels: list[Candidate] = Field(default_factory=list)
    transfers: list[Candidate] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.destinations or self.transport or self.hotels or self.transfers)


class Basket(BaseModel):
    """Подтверждённый пользователем выбор; при пересборке не перезаписывается молча."""

    model_config = ConfigDict(extra="forbid")

    items: list[PriceLine] = Field(default_factory=list)
    component_refs: dict[str, str] = Field(default_factory=dict)

    def kept_when(self, dirty_components: set[str]) -> list[str]:
        return sorted(component for component in self.component_refs if component not in dirty_components)


class RepairAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    violation_code: ViolationCode
    action_signature: str
    affected_components: list[str] = Field(default_factory=list)
    accepted: bool
    note: str | None = None


class PackageOption(BaseModel):
    """A deterministically validated alternative, distinct from the chosen basket."""
    model_config = ConfigDict(extra="forbid")

    option_id: str
    component_refs: dict[str, str]
    items: list[PriceLine]
    total: Money
    facts: dict[str, str] = Field(default_factory=dict)


class RunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calls: int = 0
    llm_calls: int = 0
    wall_clock_ms: int = 0
    tokens: int = 0
    questions_asked: int = 0
    repair_iterations: int = 0

    tool_calls_limit: int = 12
    llm_calls_limit: int = 6
    wall_clock_limit_ms: int = 20_000
    questions_limit: int = 2
    repair_iterations_limit: int = 2

    def exhausted(self) -> str | None:
        if self.tool_calls >= self.tool_calls_limit:
            return "tool_calls"
        if self.llm_calls >= self.llm_calls_limit:
            return "llm_calls"
        if self.wall_clock_ms >= self.wall_clock_limit_ms:
            return "wall_clock"
        if self.questions_asked >= self.questions_limit:
            return "questions"
        if self.repair_iterations >= self.repair_iterations_limit:
            return "repair_iterations"
        return None


class TripState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: UUID
    version: int = 0
    request: TripRequest = Field(default_factory=TripRequest)
    stage: Stage = Stage.PARSE
    run_status: RunStatus = RunStatus.RUNNING
    awaiting_fields: list[str] = Field(default_factory=list)
    candidates: Candidates = Field(default_factory=Candidates)
    chosen: Basket = Field(default_factory=Basket)
    options: list[PackageOption] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    repair_log: list[RepairAttempt] = Field(default_factory=list)
    questions_asked: int = 0
    budgets: RunBudget = Field(default_factory=RunBudget)
    provenance: dict[str, DataStatus] = Field(default_factory=dict)
    default_currency: str = "RUB"
    outcome: Outcome | None = None

    def blocking_violations(self) -> list[Violation]:
        return [violation for violation in self.violations if violation.severity.value == "blocking"]

    def next_version(self) -> TripState:
        """Версия растёт копированием: предыдущие состояния не мутируются."""

        return self.model_copy(update={"version": self.version + 1}, deep=True)
