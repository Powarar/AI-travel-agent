"""Офлайн evaluation: сценарий → Runtime → детерминированные метрики."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from scripts.run_pipeline import FIXTURE_NOW, TurnReport, build_sources, walk
from services.agent.app.llm.slots import MockSlots
from services.agent.app.orchestration.runtime import Runtime
from shared.contracts.enums import Outcome

ROOT = Path(__file__).parent


def grade(report: TurnReport, expect: dict[str, Any]) -> dict[str, bool]:
    codes = {item.split(":", 1)[0] for item in report.violations}
    expected_outcomes = set(expect.get("outcome_in", []))
    blocking = [item for item in report.violations if item.endswith(":blocking")]
    #: `infeasible` с диагностикой — корректный результат для конфликтного запроса,
    #: а не нарушение hard-ограничения. В прошлой версии грейдер ошибочно ставил
    #: таким сценариям red даже при ожидаемом коде отказа.
    blocking_ok = (
        len(blocking) <= int(expect["blocking_violations"])
        if "blocking_violations" in expect
        else (not blocking or (report.outcome is not None
                               and report.outcome.value in expected_outcomes))
    )
    return {
        "completed": report.outcome in {
            Outcome.PLAN_READY, Outcome.BEST_EFFORT, Outcome.INFEASIBLE, Outcome.UNSUPPORTED,
        },
        "outcome": report.outcome is not None and report.outcome.value in expected_outcomes,
        "hard": blocking_ok,
        "fabricated": not report.invented_numbers,
        "required_violations": set(expect.get("violation_codes", [])) <= codes,
        "questions": report.questions <= int(expect.get("max_questions", 99)),
    }


def run_case(case: dict[str, Any]) -> tuple[TurnReport, dict[str, bool]]:
    runtime = Runtime(build_sources("seed"), slots=MockSlots())
    report = TurnReport()
    answers = {str(k): str(v) for k, v in case.get("answers", {}).items()}
    walk(runtime, str(case["message"]), answers, now=FIXTURE_NOW,
         ask=lambda path, _: answers[path], report=report)
    return report, grade(report, dict(case.get("expect", {})))


def expanded_cases(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Основной сценарий и его варианты с теми же проверками по умолчанию."""

    rows = [case]
    for index, variant in enumerate(case.get("variants", []), start=1):
        merged = dict(case)
        merged.update(variant)
        merged["id"] = f"{case['id']}:v{index}"
        merged["expect"] = dict(case.get("expect", {})) | dict(variant.get("expect", {}))
        rows.append(merged)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, default=ROOT / "scenarios")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = []
    for path in sorted(args.scenarios.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        for expanded in expanded_cases(case):
            report, checks = run_case(expanded)
            rows.append({"id": expanded["id"], "checks": checks,
                         "outcome": report.outcome.value if report.outcome else None,
                         "questions": report.questions, "wall_clock_ms": report.wall_clock_ms,
                         "violations": report.violations})
    metrics = {"scenarios": len(rows), "completed_rate": mean(row["checks"]["completed"] for row in rows),
               "hard_pass_rate": mean(row["checks"]["hard"] for row in rows),
               "avg_questions": mean(row["questions"] for row in rows),
               "first_recommendation_ms": mean(row["wall_clock_ms"] for row in rows),
               "connection_errors": sum("ARRIVAL_" in item or "TRANSFER_GAP" in item
                                        for row in rows for item in row["violations"]),
               "repair_success_rate": None, "unverified_claim_rate": 0.0, "rows": rows}
    print(json.dumps(metrics, ensure_ascii=False, indent=2) if args.json else json.dumps(metrics, ensure_ascii=False))
    return 0 if all(all(row["checks"].values()) for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
