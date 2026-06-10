from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from curator.model_tasks import (
    FeedbackPlanningModelOutput,
    strict_model_json_schema,
    validate_feedback_planning_model_output,
    validate_model_response_output,
)
from curator.feedback_model_eval import (
    DEFAULT_SCENARIO_FIXTURE,
    build_feedback_scenario_base_plan,
    build_feedback_scenario_request,
    load_feedback_scenario_cases,
    score_feedback_scenario_actions,
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


@pytest.mark.parametrize(
    "case",
    load_feedback_scenario_cases(DEFAULT_SCENARIO_FIXTURE),
    ids=lambda case: case.name,
)
def test_model_feedback_planning_scenarios_match_deterministic_baseline(case: Any) -> None:
    base_plan = build_feedback_scenario_base_plan(case)

    result = score_feedback_scenario_actions(case, base_plan.proposed_actions)

    assert result.passed, result.failures


def test_model_feedback_planning_prompt_includes_feedback_comments() -> None:
    case = load_feedback_scenario_cases(DEFAULT_SCENARIO_FIXTURE)[0]

    _, request = build_feedback_scenario_request(
        case=case,
        model="eval-model",
        max_tokens=1000,
    )

    prompt_input = json.loads(request.input["messages"][1]["content"])
    comments_by_id = {
        record["feedback_id"]: record["comment"]
        for record in prompt_input["feedback_records"]
    }
    assert comments_by_id["fb_missing_home_address"] == "Home address is missing from the corpus."
    assert comments_by_id["fb_missing_beach_house_address"] == "Beach house address is missing."


def test_model_feedback_planning_response_schema_is_strict_json_schema() -> None:
    schema = strict_model_json_schema(FeedbackPlanningModelOutput)

    assert set(schema["required"]) == {"schema_version", "proposed_actions", "notes"}
    action_schema = schema["$defs"]["FeedbackPlanningModelAction"]
    assert set(action_schema["required"]) == {
        "action_type",
        "classification",
        "evidence",
        "target_repo",
    }
    assert set(action_schema["properties"]["classification"]["enum"]) == {
        "positive",
        "non_actionable",
        "owner_action",
        "corpus_candidate",
        "upload_linked",
        "capacity",
        "insufficient_evidence",
    }
    evidence_schema = schema["$defs"]["ActionEvidence"]
    assert set(evidence_schema["required"]) == {
        "feedback_ids",
        "upload_ids",
        "source_ids",
        "section_ids",
        "result_ids",
    }


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
