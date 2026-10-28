"""Typed HTTP payloads for the v1 trip API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.enums import RunStatus


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    value: str


class TripCreated(BaseModel):
    trip_id: UUID


class RunAccepted(BaseModel):
    run_id: UUID


class CheckoutCompleted(BaseModel):
    order_id: str
    idempotent_replay: bool
    simulated: Literal[True] = True


class SSEEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal[
        "run_started", "phase_changed", "llm_call", "tool_started",
        "tool_completed", "tool_failed", "question", "candidates",
        "violation", "repair_started", "plan_updated", "answer_text",
        "checkout_completed", "budget_exhausted",
    ]
    data: dict[str, object]


class RunTrace(BaseModel):
    run_id: UUID
    trip_id: UUID
    status: RunStatus
    awaiting: list[str]
    events: list[SSEEvent]
