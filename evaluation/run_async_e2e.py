"""Explicitly billable HTTP acceptance run with per-model measured observations.

The stack must already be running and the development catalogue indexed. Output
contains observations, never keys or a claim that synthetic offers are real.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx


def events(text: str) -> list[dict[str, Any]]:
    parsed = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if name and data:
            parsed.append({"event": name, "data": json.loads(data)})
    return parsed


def percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q + 0.5)))]


async def evaluate(base_url: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=75) as client:
        for attempt in range(20):
            try:
                ready = await client.get("/openapi.json")
                ready.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == 19:
                    raise
                await asyncio.sleep(0.25)
        async def turn(case: str, message: str, *, trip_id: str | None = None,
                       expected: str = "plan_ready", answers: dict[str, str] | None = None) -> str:
            if trip_id is None:
                response = await client.post("/v1/trips")
                response.raise_for_status()
                trip_id = response.json()["trip_id"]
            started = time.monotonic()
            response = await client.post(f"/v1/trips/{trip_id}/messages", json={"text": message})
            response.raise_for_status()
            trace = events(response.text)
            run_id = next(item["data"]["run_id"] for item in trace if item["event"] == "run_started")
            for _ in range(2):
                state_response = await client.get(f"/v1/trips/{trip_id}/state")
                state_response.raise_for_status()
                state = state_response.json()
                if state["run_status"] != "interrupted":
                    break
                field = state["awaiting_fields"][0]
                if answers is None or field not in answers:
                    break
                response = await client.post(f"/v1/runs/{run_id}/answers", json={"field": field, "value": answers[field]})
                response.raise_for_status()
                trace = response.json()["events"]
            state = (await client.get(f"/v1/trips/{trip_id}/state")).json()
            model_calls = [item["data"] for item in trace if item["event"] == "llm_call"]
            tools = [item["data"] for item in trace if item["event"] == "tool_completed"]
            answers_out = [item["data"] for item in trace if item["event"] == "answer_text"]
            checks = {
                "outcome": state["outcome"] == expected,
                "no_transport_failure": all(item.get("status") == "ok" for item in model_calls),
                "no_blocking_plan": expected != "plan_ready" or not any(
                    violation["severity"] == "blocking" for violation in state["violations"]),
                "russian_response": bool(answers_out) and all("cannot read" not in item["text"] for item in answers_out),
            }
            if case == "hybrid_and_rerank":
                checks["hybrid_executed"] = any(tool.get("retrieval_mode") == "hybrid" for tool in tools)
                checks["rerank_executed"] = any(call["slot"] == "rerank" for call in model_calls)
                checks["multiple_options"] = len(state.get("options", [])) >= 2
                checks["pool_requirement"] = "pool" in state["request"]["lodging"]["must"]["value"]
            if case == "flight_only_followup":
                checks["selective_services"] = {tool["tool"] for tool in tools} <= {"transport_search", "transfer_search"}
                checks["flight_searched"] = any(tool["tool"] == "transport_search" for tool in tools)
            row = {"case": case, "trip_id": trip_id, "outcome": state["outcome"], "checks": checks,
                   "wall_clock_ms": round((time.monotonic() - started) * 1000), "models": model_calls,
                   "services": tools, "answer": answers_out, "options": len(state.get("options", []))}
            rows.append(row)
            print(json.dumps({"case": case, "checks": checks, "ms": row["wall_clock_ms"]}, ensure_ascii=False), flush=True)
            return trip_id

        trip = await turn("hybrid_and_rerank", "Хочу в Сочи с 14 по 21 ноября из Москвы вдвоём, "
                          "бюджет 200000 рублей. Нужен тихий отель, обязательно с бассейном.")
        await turn("flight_only_followup", "Только прямые рейсы", trip_id=trip)
        await turn("clarification", "Хочу в Сочи с 14 по 21 ноября вдвоём, бюджет 200000 рублей",
                   answers={"origin": "Москва"})
        await turn("impossible_budget", "Хочу в Сочи с 14 по 21 ноября из Москвы вдвоём, бюджет 10000 рублей",
                   expected="infeasible")
        await turn("unknown_destination", "Хочу в Гамбург с 14 по 21 ноября из Москвы вдвоём, бюджет 200000 рублей",
                   expected="infeasible")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for call in row["models"]:
            grouped[call["slot"] + ":" + call["model"]].append(call)
    models = {key: {"calls": len(calls), "p50_ms": percentile([c["latency_ms"] for c in calls], .5),
                    "p95_ms": percentile([c["latency_ms"] for c in calls], .95),
                    "tokens": sum(c.get("total_tokens") or 0 for c in calls),
                    "http_errors": sum(c["status"] != "ok" for c in calls)} for key, calls in grouped.items()}
    return {"at": datetime.now(timezone.utc).isoformat(), "inventory": "synthetic development catalogue",
            "cases": len(rows), "passed": sum(all(row["checks"].values()) for row in rows),
            "functional_passed": sum(all(value for key, value in row["checks"].items()
                                          if key != "no_transport_failure") for row in rows),
            "models": models, "rows": rows}


def main() -> int:
    if os.environ.get("RUN_LIVE_EVALS") != "1":
        raise SystemExit("Set RUN_LIVE_EVALS=1 to enable billable calls")
    report = asyncio.run(evaluate(os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8010")))
    destination = Path("artifacts/async-e2e.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.rename(destination.with_name("async-e2e-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"))
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(destination), "passed": report["passed"], "cases": report["cases"],
                      "models": report["models"]}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
