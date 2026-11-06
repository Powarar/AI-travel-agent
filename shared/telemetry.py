"""Task-local model observations. Prompts and secrets never enter this sink."""
from contextvars import ContextVar
from typing import Any

model_spans: ContextVar[list[dict[str, Any]] | None] = ContextVar("model_spans", default=None)
