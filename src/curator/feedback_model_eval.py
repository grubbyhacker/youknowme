from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from curator.model_tasks import FeedbackPlanningModelOutput, build_feedback_planning_proposed_actions
from curator.models import (
    FeedbackPlan,
    FeedbackWindow,
    ModelCallBudget,
    ModelCallRequest,
    ProposedAction,
)
from curator.planning import build_feedback_plan
from curator.runner import _feedback_planning_model_request


DEFAULT_SCENARIO_FIXTURE = Path("fixtures/curator/model-feedback-planning/scenarios.json")


class ExpectedFeedbackOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    classification: str
    target_repo: str | None = None
    upload_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)


class FeedbackScenarioCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    feedback_records: list[dict[str, Any]]
    expected_feedback: dict[str, ExpectedFeedbackOutcome]
    soft_action_threshold: int = Field(default=20, ge=0)


class FeedbackScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: str
    passed: bool
    expected_count: int
    passed_count: int
    failures: list[str] = Field(default_factory=list)


def load_feedback_scenario_cases(
    path: Path = DEFAULT_SCENARIO_FIXTURE,
    *,
    names: set[str] | None = None,
) -> list[FeedbackScenarioCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [FeedbackScenarioCase.model_validate(case) for case in payload["cases"]]
    if names is not None:
        cases = [case for case in cases if case.name in names]
    return cases


def build_feedback_scenario_base_plan(case: FeedbackScenarioCase) -> FeedbackPlan:
    return build_feedback_plan(
        run_id=f"eval-{case.name}",
        feedback_window=FeedbackWindow(start_offset=0, end_offset=len(case.feedback_records)),
        feedback_records=case.feedback_records,
        latest_decisions={},
        soft_action_threshold=case.soft_action_threshold,
    )


def build_feedback_scenario_request(
    *,
    case: FeedbackScenarioCase,
    model: str,
    max_tokens: int,
    run_id_prefix: str = "eval",
) -> tuple[FeedbackPlan, ModelCallRequest]:
    run_id = f"{run_id_prefix}-{case.name}"
    base_plan = build_feedback_plan(
        run_id=run_id,
        feedback_window=FeedbackWindow(start_offset=0, end_offset=len(case.feedback_records)),
        feedback_records=case.feedback_records,
        latest_decisions={},
        soft_action_threshold=case.soft_action_threshold,
    )
    request = _feedback_planning_model_request(
        run_id=run_id,
        model=model,
        model_call_budget=ModelCallBudget(max_calls_per_run=1, max_tokens_per_run=max_tokens),
        base_plan=base_plan,
        feedback_records=case.feedback_records,
    )
    return base_plan, request


def score_feedback_scenario_output(
    case: FeedbackScenarioCase,
    base_plan: FeedbackPlan,
    output: FeedbackPlanningModelOutput,
) -> FeedbackScenarioResult:
    actions = build_feedback_planning_proposed_actions(output, base_plan=base_plan)
    return score_feedback_scenario_actions(case, actions)


def score_feedback_scenario_actions(
    case: FeedbackScenarioCase,
    actions: list[ProposedAction],
) -> FeedbackScenarioResult:
    failures: list[str] = []
    passed_count = 0

    for feedback_id, expected in case.expected_feedback.items():
        matching = [
            action for action in actions if feedback_id in set(action.evidence.feedback_ids)
        ]
        if not matching:
            failures.append(f"{feedback_id}: no action covers feedback_id")
            continue
        if len(matching) > 1:
            failures.append(f"{feedback_id}: covered by multiple actions")
            continue
        action = matching[0]
        mismatches = _action_mismatches(action, expected)
        if mismatches:
            failures.extend(f"{feedback_id}: {mismatch}" for mismatch in mismatches)
            continue
        passed_count += 1

    return FeedbackScenarioResult(
        case=case.name,
        passed=not failures,
        expected_count=len(case.expected_feedback),
        passed_count=passed_count,
        failures=failures,
    )


def _action_mismatches(
    action: ProposedAction,
    expected: ExpectedFeedbackOutcome,
) -> list[str]:
    mismatches: list[str] = []
    if action.action_type != expected.action_type:
        mismatches.append(f"action_type expected {expected.action_type}, got {action.action_type}")
    if action.classification != expected.classification:
        mismatches.append(
            f"classification expected {expected.classification}, got {action.classification}"
        )
    if expected.target_repo is not None and action.target_repo != expected.target_repo:
        mismatches.append(f"target_repo expected {expected.target_repo}, got {action.target_repo}")
    for evidence_field in ("upload_ids", "source_ids", "section_ids", "result_ids"):
        expected_ids = set(getattr(expected, evidence_field))
        if not expected_ids:
            continue
        actual_ids = set(getattr(action.evidence, evidence_field))
        missing_ids = sorted(expected_ids - actual_ids)
        if missing_ids:
            mismatches.append(f"{evidence_field} missing {missing_ids}")
    return mismatches
