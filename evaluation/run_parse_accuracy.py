"""Labelled async parse evaluation. Fallback results never earn model credit."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from services.agent.app.llm.async_provider import AsyncSlots, async_slots_from_config
from services.agent.app.llm.slots import MockSlots
from shared.contracts.money import Money
from shared.contracts.request import FieldValue, TripRequest
from shared.evaluation_metrics import parse_accuracy_summary


def normalize(value: Any) -> Any:
    if isinstance(value, Money):
        return {"amount": str(value.amount), "currency": value.currency}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.casefold().strip()
    return value


def matches(path: str, actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and "amount" in expected:
        return isinstance(actual, Money) and actual.currency == expected["currency"] and actual.amount == Decimal(expected["amount"])
    if path in {"origin", "direction.explicit"} and isinstance(expected, list):
        return normalize(actual) in [normalize(alias) for alias in expected]
    if path == "transport.max_hours_direct":
        return actual is not None and Decimal(str(actual)) == Decimal(str(expected))
    return normalize(actual) == normalize(expected)


def grade(case: dict[str, Any], request: TripRequest, *, fallback: bool) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    actual_values: dict[str, Any] = {}
    for path, expected in case["expected"].items():
        field = request.field_value(path)
        actual_values[path] = normalize(field.value) if isinstance(field, FieldValue) else None
        checks[path] = isinstance(field, FieldValue) and matches(path, field.value, expected)
    for path in case.get("forbidden", []):
        field = request.field_value(path)
        checks["absent:" + path] = isinstance(field, FieldValue) and field.value is None
    preferences = " ".join(request.soft_preferences).casefold()
    for fragment in case.get("preferences_contain", []):
        checks["preference:" + fragment] = fragment.casefold() in preferences
    return {"case": case["id"], "checks": checks, "fallback": fallback,
            "actual": actual_values, "expected": case["expected"],
            "required_fields": len(case["expected"]),
            "required_correct": 0 if fallback else sum(checks[path] for path in case["expected"]),
            "system_correct": sum(checks.values()), "model_correct": 0 if fallback else sum(checks.values()),
            "fields": len(checks), "exact_match": bool(checks) and all(checks.values()) and not fallback}


async def evaluate(dataset: dict[str, Any], *, live: bool) -> dict[str, Any]:
    slots = async_slots_from_config() if live else MockSlots()
    if live and not isinstance(slots, AsyncSlots):
        raise ValueError("Live accuracy requires a configured real provider")
    rows = []
    try:
        for case in dataset["cases"]:
            before = len(getattr(slots, "failures", []))
            request = await slots.parse(case["message"]) if isinstance(slots, AsyncSlots) else slots.parse(case["message"])
            row = grade(case, request, fallback=len(getattr(slots, "failures", [])) > before)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        spans = getattr(slots, "spans", [])
        known_costs = [Decimal(span["estimated_cost_rub"]) for span in spans if span.get("estimated_cost_rub") is not None]
        return {"created_at": datetime.now(timezone.utc).isoformat(), "dataset_id": dataset["dataset_id"],
                "model": slots.config._route("parse")[0] if isinstance(slots, AsyncSlots) else "deterministic-offline",
                "prompt_version": slots.prompt_versions.get("parse", "unknown") if isinstance(slots, AsyncSlots) else "offline",
                "mode": "live" if live else "offline",
                **parse_accuracy_summary(rows),
                "known_estimated_cost_rub": str(sum(known_costs, Decimal(0))),
                "unpriced_calls": len(spans) - len(known_costs), "rows": rows, "spans": spans}
    finally:
        if isinstance(slots, AsyncSlots):
            await slots.close()


async def save(report: dict[str, Any]) -> None:
    dsn = os.environ.get("DATABASE_URL", "postgresql://ostrovok:ostrovok@localhost:5434/ostrovok")
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        await conn.execute("INSERT INTO evaluation_runs(eval_id,dataset_id,dataset_hash,model,prompt_version,mode,report) "
                           "VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)",
                           uuid4(), report["dataset_id"], report["dataset_hash"], report["model"],
                           report["prompt_version"], report["mode"], json.dumps(report, ensure_ascii=False))
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.live and os.environ.get("RUN_LIVE_EVALS") != "1":
        raise SystemExit("Set RUN_LIVE_EVALS=1 to authorize billable evaluation")
    path = Path(__file__).parent / "datasets/parse_ru_v1.json"
    dataset = json.loads(path.read_text())
    if args.limit:
        dataset["cases"] = dataset["cases"][:args.limit]
    report = asyncio.run(evaluate(dataset, live=args.live))
    report["dataset_hash"] = hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report["limited"] = args.limit is not None
    target = Path("artifacts") / ("parse-accuracy-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if args.persist:
        asyncio.run(save(report))
    print(json.dumps({key: value for key, value in report.items() if key not in {"rows", "spans"}}, ensure_ascii=False, indent=2))
    print("Report:", target)
    return 0 if report["accuracy"] == 1 and report["fallback_cases"] == 0 and report["exact_match_rate"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
