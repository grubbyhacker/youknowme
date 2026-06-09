from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from curator.model_tasks import (
    FeedbackPlanningModelOutput,
    validate_feedback_planning_model_output,
    validate_model_response_output,
)
from curator.models import FeedbackPlan, FeedbackWindow, ModelCallResponse
from curator.planning import build_feedback_plan


FIXTURE_PATH = Path("fixtures/curator/model-feedback-planning/cases.json")


def _fixture_cases(key: str) -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload[key]


@pytest.mark.parametrize(
    "case",
    _fixture_cases("valid_cases"),
    ids=lambda case: case["name"],
)
def test_model_feedback_planning_eval_valid_cases(case: dict[str, Any]) -> None:
    base_plan = _base_plan(
        case["name"],
        case["feedback_records"],
        soft_action_threshold=case.get("soft_action_threshold", 10),
    )
    output = _model_output(case["model_output"])

    validate_feedback_planning_model_output(base_plan, output)


@pytest.mark.parametrize(
    "case",
    _fixture_cases("invalid_cases"),
    ids=lambda case: case["name"],
)
def test_model_feedback_planning_eval_rejects_bad_outputs(case: dict[str, Any]) -> None:
    base_plan = _base_plan(
        case["name"],
        case["feedback_records"],
        soft_action_threshold=case.get("soft_action_threshold", 10),
    )

    with pytest.raises((ValueError, ValidationError), match=case["match"]):
        output = _model_output(case["model_output"])
        validate_feedback_planning_model_output(base_plan, output)


def test_model_feedback_planning_eval_uses_model_response_contract() -> None:
    case = _fixture_cases("valid_cases")[0]
    response = ModelCallResponse(
        task_name="feedback_plan",
        output=_materialize_output(case["model_output"]),
    )

    output = validate_model_response_output(
        response,
        FeedbackPlanningModelOutput,
        expected_task_name="feedback_plan",
    )

    assert output.proposed_actions[0].evidence.feedback_ids == ["fb_positive"]


def _base_plan(
    run_id: str,
    feedback_records: list[dict[str, Any]],
    *,
    soft_action_threshold: int,
) -> FeedbackPlan:
    return build_feedback_plan(
        run_id=f"eval-{run_id}",
        feedback_window=FeedbackWindow(start_offset=0, end_offset=len(feedback_records)),
        feedback_records=feedback_records,
        latest_decisions={},
        soft_action_threshold=soft_action_threshold,
    )


def _model_output(raw_output: dict[str, Any]) -> FeedbackPlanningModelOutput:
    return FeedbackPlanningModelOutput.model_validate(_materialize_output(raw_output))


def _materialize_output(raw_output: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(raw_output)
