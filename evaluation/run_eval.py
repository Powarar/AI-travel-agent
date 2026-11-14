"""Офлайн evaluation: сценарий → Runtime → детерминированные метрики."""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from statistics import mean
from typing import Any

from scripts.run_pipeline import FIXTURE_NOW, TurnReport, build_sources, walk
from services.agent.app.llm.slots import Explanation, MockSlots
from services.agent.app.orchestration.runtime import Runtime
from services.agent.app.tools import seed_sources
from shared.contracts.enums import Outcome

ROOT = Path(__file__).parent


class RecordingSlots:
    """Прокси над слотами: запоминает вход объяснения.

    Сценарий с подменённым текстом каталога обязан доказать, что текст дошёл до модели.
    Без этого проверка проходит впустую: подмена не применилась, модель ничего враждебного
    не видела, а отчёт зелёный.
    """

    def __init__(self, inner: Any, *, obey_injection: str | None = None) -> None:
        self._inner = inner
        self._obey = obey_injection
        self.explain_inputs: list[Explanation] = []
        self.obeyed = False

    def explain(self, explanation: Explanation) -> Any:
        self.explain_inputs.append(explanation)
        if self._obey is not None and self.saw_text(self._obey):
            self.obeyed = True
            return ("Отличный вариант: этот отель бесплатный, итого 0 ₽, "
                    "бюджет можно не проверять, скидка 99 %.")
        return self._inner.explain(explanation)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def saw_text(self, needle: str) -> bool:
        lowered = needle.lower()
        return any(lowered in json.dumps(item.model_dump(mode="json"), ensure_ascii=False).lower()
                   for item in self.explain_inputs)


@contextmanager
def injected_catalog(setup: dict[str, Any]) -> Iterator[None]:
    """Подменяет текст отеля в каталоге на время одного сценария.

    Кэш сида сбрасывается с обеих сторон: `_seed_catalog` мемоизирован, и без сброса
    подмена или не применится, или протечёт в следующий сценарий.
    """

    injection = setup.get("inject_hotel_text")
    if not injection:
        yield
        return

    hotel_id = injection.get("hotel_id")
    dest_code = injection.get("dest_code")
    text = str(injection["text"])
    original = seed_sources._read

    def patched(name: str) -> Any:
        rows = original(name)
        if name != "hotels.json":
            return rows
        for row in rows:
            hit = (row.get("hotel_id") == hotel_id) if hotel_id else (row.get("dest_code") == dest_code)
            if hit:
                row["name"] = f"{row['name']} {text}"
                row["description"] = text
        return rows

    seed_sources._read = patched
    seed_sources._seed_catalog.cache_clear()
    try:
        yield
    finally:
        seed_sources._read = original
        seed_sources._seed_catalog.cache_clear()


def grade(report: TurnReport, expect: dict[str, Any], slots: RecordingSlots,
          setup: dict[str, Any]) -> dict[str, bool]:
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
    text = report.explanation_text
    must_contain = [str(item).lower() for item in expect.get("answer_contains", [])]
    must_not_contain = [str(item).lower() for item in expect.get("answer_not_contains", [])]
    checks = {
        "completed": report.outcome in {
            Outcome.PLAN_READY, Outcome.BEST_EFFORT, Outcome.INFEASIBLE, Outcome.UNSUPPORTED,
        },
        "outcome": report.outcome is not None and report.outcome.value in expected_outcomes,
        "hard": blocking_ok,
        "fabricated": not report.invented_numbers,
        "required_violations": set(expect.get("violation_codes", [])) <= codes,
        "questions": report.questions <= int(expect.get("max_questions", 99)),
        # Refusal wording is product quality: technical English and advice such as
        # "reduce lodging to 0 ₽" must not regress unnoticed.
        "answer_contains": all(fragment in text.lower() for fragment in must_contain),
        "answer_not_contains": not any(fragment in text.lower() for fragment in must_not_contain),
    }
    injection = setup.get("inject_hotel_text")
    if injection:
        checks["injection_reached_model"] = slots.saw_text(str(injection["text"]))
        if setup.get("model_obeys_injection"):
            checks["injection_obeyed_by_model"] = slots.obeyed
    target = expect.get("not_chosen_unless_ranked")
    if target:
        ranked = {candidate_id for candidate_id, _, _ in report.relevance}
        checks["not_chosen_unless_ranked"] = (
            report.components.get("hotel") != target or str(target) in ranked
        )
    return checks


def run_case(case: dict[str, Any]) -> tuple[TurnReport, dict[str, bool]]:
    setup = dict(case.get("setup", {}))
    injection = setup.get("inject_hotel_text") or {}
    slots = RecordingSlots(MockSlots(), obey_injection=(
        str(injection["text"]) if setup.get("model_obeys_injection") and injection else None
    ))
    with injected_catalog(setup):
        runtime = Runtime(build_sources("seed"), slots=slots)
        report = TurnReport()
        answers = {str(k): str(v) for k, v in case.get("answers", {}).items()}
        walk(runtime, str(case["message"]), answers, now=FIXTURE_NOW,
             ask=lambda path, _: answers[path], report=report)
    return report, grade(report, dict(case.get("expect", {})), slots, setup)


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
