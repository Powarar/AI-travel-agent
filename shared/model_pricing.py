"""Versioned estimates from user-supplied rates, not a provider billing statement."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

DEFAULT_PRICES = {
    "deepseek-v4.1-flash-1017": {"input": "30", "output": "120"},
    "qwen3.7-plus": {"input": "64", "output": "256"},
    "qwen3-embedding-8b": {"total": "2"},
}


def price_span(span: dict[str, Any]) -> dict[str, Any]:
    version = os.environ.get("MODEL_PRICES_VERSION", "user-rates-2026-11-05")
    result: dict[str, Any] = {"estimated_cost_rub": None, "pricing_version": version,
                              "cost_status": "unknown_model_rate", "cost_kind": "estimate"}
    try:
        prices = json.loads(os.environ.get("MODEL_PRICES_JSON") or "null")
    except ValueError:
        return {**result, "cost_status": "invalid_pricing_config"}
    if prices is None:
        prices = DEFAULT_PRICES
    if not isinstance(prices, dict):
        return {**result, "cost_status": "invalid_pricing_config"}
    rate = prices.get(str(span.get("model", "")))
    if rate is None:
        return result
    if not isinstance(rate, dict) or not ("total" in rate or {"input", "output"} <= rate.keys()):
        return {**result, "cost_status": "invalid_pricing_config"}
    result["rate_rub_per_million"] = rate
    keys = ("total",) if "total" in rate else ("input", "output")
    tokens = [span.get(key + "_tokens") for key in keys]
    if any(type(value) is not int or value < 0 for value in tokens):
        result["cost_status"] = "missing_usage"
        return result
    try:
        rates = [Decimal(str(rate[key])) for key in keys if type(rate[key]) in {int, str}]
        if len(rates) != len(keys) or any(not value.is_finite() or value < 0 for value in rates):
            raise ValueError("invalid rate")
        cost = sum((Decimal(str(count)) * value for value, count in zip(rates, tokens)), Decimal(0))
    except (ValueError, ArithmeticError):
        return {**result, "cost_status": "invalid_pricing_config"}
    result.update(estimated_cost_rub=str(cost / Decimal(1_000_000)), cost_status="estimated")
    return result
