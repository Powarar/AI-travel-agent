"""Model telemetry aggregation. Missing usage and unknown rates stay explicit."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from shared.model_pricing import price_span


def summarize_costs(spans: list[dict[str, Any]]) -> dict[str, Any]:
    total = Decimal(0)
    priced = 0
    historical = 0
    reasons: dict[str, int] = {}
    for span in spans:
        estimate = span if "cost_status" in span else price_span(span)
        if estimate.get("estimated_cost_rub") is not None:
            total += Decimal(str(estimate["estimated_cost_rub"]))
            priced += 1
            historical += int("cost_status" not in span)
        else:
            reason = estimate.get("cost_status", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"known_estimated_cost_rub": str(total), "priced_calls": priced,
            "unpriced_calls": len(spans) - priced, "unpriced_reasons": reasons,
            "historically_reestimated_calls": historical,
            "cost_coverage": priced / len(spans) if spans else None,
            "currency": "RUB", "cost_kind": "estimate"}
