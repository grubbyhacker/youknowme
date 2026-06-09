from __future__ import annotations

from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from curator.models import (
    CURATOR_SCHEMA_VERSION,
    DEFAULT_TARGET_REPO,
    ActionEvidence,
    CuratorPrState,
    FeedbackPlan,
    ModelCallResponse,
    ProposedAction,
    UploadDecision,
)
from curator.state import deterministic_idempotency_key


class FeedbackPlanningModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=500)


class UploadReviewModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    upload_id: str
    decision: UploadDecision
    reason: str = Field(max_length=500)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)


class PrCommentClassificationModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    pr_number: int
    pr_state: CuratorPrState
    reason: str = Field(max_length=500)
    needs_owner_input: bool = False


class PrBodyDraftModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


ModelOutputT = TypeVar("ModelOutputT", bound=BaseModel)


def validate_model_response_output(
    response: ModelCallResponse,
    output_model: type[ModelOutputT],
    *,
    expected_task_name: str | None = None,
) -> ModelOutputT:
    if expected_task_name is not None and response.task_name != expected_task_name:
        raise ValueError(
            f"model response task mismatch: expected {expected_task_name}, got {response.task_name}"
        )
    return output_model.model_validate(response.output)


def validate_feedback_planning_model_output(
    base_plan: FeedbackPlan,
    output: FeedbackPlanningModelOutput,
) -> None:
    allowed_feedback_ids = set(base_plan.included_feedback_ids)
    allowed_upload_ids = set(base_plan.referenced_upload_ids)
    allowed_source_ids = set(base_plan.referenced_source_ids)
    allowed_section_ids = set(base_plan.referenced_section_ids)
    allowed_result_ids = set(base_plan.referenced_result_ids)
    seen_action_ids: set[str] = set()
    seen_idempotency_keys: set[str] = set()
    covered_feedback_ids: set[str] = set()

    for action in output.proposed_actions:
        if action.action_id in seen_action_ids:
            raise ValueError(f"model action duplicates action_id: {action.action_id}")
        seen_action_ids.add(action.action_id)
        if action.idempotency_key in seen_idempotency_keys:
            raise ValueError(
                f"model action duplicates idempotency_key: {action.idempotency_key}"
            )
        seen_idempotency_keys.add(action.idempotency_key)
        _validate_evidence_subset(
            action.evidence,
            allowed_feedback_ids=allowed_feedback_ids,
            allowed_upload_ids=allowed_upload_ids,
            allowed_source_ids=allowed_source_ids,
            allowed_section_ids=allowed_section_ids,
            allowed_result_ids=allowed_result_ids,
        )
        expected_key = deterministic_idempotency_key(action.action_type, action.evidence)
        if action.idempotency_key != expected_key:
            raise ValueError(
                "model action idempotency_key does not match action evidence: "
                f"{action.action_id}"
            )
        if action.action_type == "corpus_pr" and not (
            action.evidence.source_ids
            or action.evidence.section_ids
            or action.evidence.upload_ids
        ):
            raise ValueError("model corpus_pr action must cite source, section, or upload evidence")
        if action.action_type == "link_to_upload" and not action.evidence.upload_ids:
            raise ValueError("model link_to_upload action must cite upload evidence")
        if action.action_type in {"issue", "corpus_pr"} and action.target_repo != DEFAULT_TARGET_REPO:
            raise ValueError(
                "model GitHub-object action must target the allowed corpus repo: "
                f"{DEFAULT_TARGET_REPO}"
            )
        covered_feedback_ids.update(action.evidence.feedback_ids)

    missing_feedback_ids = sorted(allowed_feedback_ids - covered_feedback_ids)
    if missing_feedback_ids:
        raise ValueError(f"model actions do not cover included feedback_ids: {missing_feedback_ids}")


def _validate_evidence_subset(
    evidence: ActionEvidence,
    *,
    allowed_feedback_ids: set[str],
    allowed_upload_ids: set[str],
    allowed_source_ids: set[str],
    allowed_section_ids: set[str],
    allowed_result_ids: set[str],
) -> None:
    subsets = {
        "feedback_ids": (set(evidence.feedback_ids), allowed_feedback_ids),
        "upload_ids": (set(evidence.upload_ids), allowed_upload_ids),
        "source_ids": (set(evidence.source_ids), allowed_source_ids),
        "section_ids": (set(evidence.section_ids), allowed_section_ids),
        "result_ids": (set(evidence.result_ids), allowed_result_ids),
    }
    for name, (actual, allowed) in subsets.items():
        unknown = sorted(actual - allowed)
        if unknown:
            raise ValueError(f"model action cites unknown {name}: {unknown}")
