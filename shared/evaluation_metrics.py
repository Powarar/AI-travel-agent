"""Reproducible summaries of labelled checks; fallback is never model evidence."""
from typing import Any


def parse_accuracy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required = sum(row["required_fields"] for row in rows)
    available = sum(row["required_fields"] for row in rows if not row["fallback"])
    correct = sum(row["required_correct"] for row in rows if not row["fallback"])
    assertions = sum(len(row["checks"]) for row in rows)
    correct_assertions = sum(sum(row["checks"].values()) for row in rows if not row["fallback"])
    return {
        "metric_definition_version": "parse-field-v2",
        "cases": len(rows), "required_fields": required, "available_required_fields": available,
        "correct_fields": correct, "field_checks": assertions, "correct_assertions": correct_assertions,
        "accuracy": correct / available if available else None,
        "accuracy_status": "measured" if available else "no_successful_model_samples",
        "effective_accuracy": correct / required if required else None,
        "assertion_accuracy": correct_assertions / assertions if assertions else None,
        "system_accuracy": sum(sum(row["checks"].values()) for row in rows) / assertions if assertions else None,
        "exact_match_rate": sum(bool(row["checks"]) and all(row["checks"].values()) and not row["fallback"]
                                for row in rows) / len(rows) if rows else None,
        "fallback_cases": sum(row["fallback"] for row in rows),
        "successful_cases": sum(not row["fallback"] for row in rows),
    }
