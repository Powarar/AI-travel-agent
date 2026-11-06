"""Measurement correctness: decimal cost, incomplete usage and unbiased labels."""
from decimal import Decimal

from shared.model_pricing import price_span
from services.backend.app.metrics.models import summarize_costs
from evaluation.run_parse_accuracy import grade
from shared.contracts.request import FieldValue, TripRequest
from shared.contracts.enums import ConstraintKind
from shared.evaluation_metrics import parse_accuracy_summary
import pytest
import httpx
import json
from services.agent.app.llm.async_provider import AsyncSlots
from services.agent.app.llm.provider_openai_compatible import OpenAICompatibleSlots


def test_cost_is_decimal_and_keeps_sub_kopeck_precision():
    value = price_span({"model": "deepseek-v4.1-flash-1017", "input_tokens": 1000, "output_tokens": 100})
    assert Decimal(value["estimated_cost_rub"]) == Decimal("0.042")
    assert value["cost_kind"] == "estimate"
    assert value["pricing_version"] == "user-rates-2026-11-05"


def test_embedding_cost_uses_reported_total_tokens():
    value = price_span({"model": "qwen3-embedding-8b", "total_tokens": 7})
    assert Decimal(value["estimated_cost_rub"]) == Decimal("0.000014")


def test_unknown_tariff_and_missing_usage_are_not_free():
    unknown = price_span({"model": "qwen3-reranker-8b", "total_tokens": 2000})
    missing = price_span({"model": "qwen3.7-plus", "input_tokens": 1000})
    assert unknown["estimated_cost_rub"] is None and unknown["cost_status"] == "unknown_model_rate"
    assert missing["estimated_cost_rub"] is None and missing["cost_status"] == "missing_usage"


def test_partial_cost_exposes_coverage():
    report = summarize_costs([
        {"model": "qwen3-embedding-8b", "total_tokens": 7},
        {"model": "qwen3-reranker-8b", "total_tokens": 2000},
    ])
    assert report["known_estimated_cost_rub"] == "0.000014"
    assert report["unpriced_calls"] == 1
    assert report["cost_coverage"] == 0.5


def test_fallback_gets_no_model_credit_even_when_system_answer_is_correct():
    request = TripRequest()
    request.nights = FieldValue[int].known(7, kind=ConstraintKind.HARD)
    result = grade({"id": "nights", "expected": {"nights": 7}}, request, fallback=True)
    assert result["system_correct"] == 1
    assert result["model_correct"] == result["required_correct"] == 0
    assert not result["exact_match"]


def test_city_aliases_do_not_accept_fuzzy_prefix_matches():
    request = TripRequest()
    request.origin = FieldValue[str].known("Москва чужой город", kind=ConstraintKind.HARD)
    result = grade({"id": "city", "expected": {"origin": ["Москва", "Москвы"]}}, request, fallback=False)
    assert not result["checks"]["origin"]


def test_unavailable_model_has_no_conditional_accuracy():
    row = {"required_fields": 1, "required_correct": 0, "checks": {"nights": True}, "fallback": True}
    report = parse_accuracy_summary([row])
    assert report["accuracy"] is None
    assert report["effective_accuracy"] == 0
    assert report["system_accuracy"] == 1


def test_accuracy_denominators_do_not_mix_absence_checks_and_required_values():
    row = {"required_fields": 2, "required_correct": 1,
           "checks": {"a": True, "b": False, "absent:c": True}, "fallback": False}
    report = parse_accuracy_summary([row])
    assert report["accuracy"] == 0.5
    assert report["correct_fields"] == 1
    assert report["assertion_accuracy"] == 2 / 3


@pytest.mark.asyncio
async def test_real_async_provider_boundary_records_usage_price_and_prompt_hash():
    payload = {"deltas": [{"path": "nights", "raw": "7 ночей", "kind": "hard"}]}
    async def respond(request):
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)},
                                                       "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                                                 "total_tokens": 1100}})
    async with httpx.AsyncClient(base_url="https://test.invalid", transport=httpx.MockTransport(respond)) as client:
        config = OpenAICompatibleSlots(model="deepseek-v4.1-flash-1017", api_key="test-only", year=2026)
        slots = AsyncSlots(config, client=client)
        request = await slots.parse("На 7 ночей")
        assert request.nights.value == 7
        assert slots.spans[0]["total_tokens"] == 1100
        assert Decimal(slots.spans[0]["estimated_cost_rub"]) == Decimal("0.042")
        assert len(slots.spans[0]["prompt_version"]) == 16


def test_bad_pricing_config_does_not_break_inference(monkeypatch):
    monkeypatch.setenv("MODEL_PRICES_JSON", "invalid-json")
    assert price_span({"model": "qwen3.7-plus"})["cost_status"] == "invalid_pricing_config"
