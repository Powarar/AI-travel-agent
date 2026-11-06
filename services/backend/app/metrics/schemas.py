"""Public, aggregation-only monitoring contracts (no prompts or personal data)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OutcomeMetrics(BaseModel):
    runs: int = 0
    plan_ready: int = 0
    infeasible: int = 0
    best_effort: int = 0
    unsupported: int = 0
    awaiting_answer: int = 0
    errors: int = 0
    fallbacks: int = 0


class QualityMetrics(BaseModel):
    hard_pass_rate: float | None = None
    unverified_claim_rate: float | None = None
    evaluation_scenarios: int | None = None


class MonitoringSummary(BaseModel):
    window: str = "since process start"
    outcomes: OutcomeMetrics
    avg_wall_clock_ms: float = 0
    avg_llm_calls: float = 0
    avg_tool_calls: float = 0
    avg_questions: float = 0
    tool_errors: dict[str, int] = Field(default_factory=dict)
    quality: QualityMetrics
