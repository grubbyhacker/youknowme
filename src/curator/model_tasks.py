from __future__ import annotations

import copy
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from curator.models import (
    CURATOR_SCHEMA_VERSION,
    DEFAULT_TARGET_REPO,
    ActionEvidence,
    CuratorActionType,
    CuratorPrState,
    FeedbackPlan,
    ModelCallResponse,
    ProposedAction,
    UploadDecision,
)
from curator.state import deterministic_idempotency_key


FeedbackPlanningClassification = Literal[
    "positive",
    "non_actionable",
    "owner_action",
    "corpus_candidate",
    "upload_linked",
    "capacity",
    "insufficient_evidence",
]


class FeedbackPlanningModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: CuratorActionType
    classification: FeedbackPlanningClassification
    evidence: ActionEvidence = Field(default_factory=ActionEvidence)
    target_repo: str | None = None


class FeedbackPlanningModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    proposed_actions: list[FeedbackPlanningModelAction] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=500)


class UploadReviewDraftFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)


class UploadReviewPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_roots_add: list[str] = Field(default_factory=list)
    allowed_types_add: list[str] = Field(default_factory=list)
    allowed_tags_add: list[str] = Field(default_factory=list)


class UploadReviewModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    upload_id: str
    decision: UploadDecision
    files: list[UploadReviewDraftFile] = Field(default_factory=list)
    policy_patch: UploadReviewPolicyPatch = Field(default_factory=UploadReviewPolicyPatch)
    rationale: str = Field(max_length=1000)
    reason: str = Field(max_length=500)


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


def strict_model_json_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    schema = copy.deepcopy(output_model.model_json_schema())
    _require_all_object_properties(schema)
    return schema


def validate_feedback_planning_model_output(
    base_plan: FeedbackPlan,
    output: FeedbackPlanningModelOutput,
) -> None:
    build_feedback_planning_proposed_actions(output, base_plan=base_plan)


def build_feedback_planning_proposed_actions(
    output: FeedbackPlanningModelOutput,
    *,
    base_plan: FeedbackPlan,
) -> list[ProposedAction]:
    allowed_feedback_ids = set(base_plan.included_feedback_ids)
    allowed_upload_ids = set(base_plan.referenced_upload_ids)
    allowed_source_ids = set(base_plan.referenced_source_ids)
    allowed_section_ids = set(base_plan.referenced_section_ids)
    allowed_result_ids = set(base_plan.referenced_result_ids)
    deterministic_action_type_by_feedback_id = _deterministic_action_types(base_plan)
    seen_idempotency_keys: set[str] = set()
    covered_feedback_ids: set[str] = set()
    proposed_actions: list[ProposedAction] = []

    for index, action in enumerate(output.proposed_actions, start=1):
        _validate_evidence_subset(
            action.evidence,
            allowed_feedback_ids=allowed_feedback_ids,
            allowed_upload_ids=allowed_upload_ids,
            allowed_source_ids=allowed_source_ids,
            allowed_section_ids=allowed_section_ids,
            allowed_result_ids=allowed_result_ids,
        )
        idempotency_key = deterministic_idempotency_key(action.action_type, action.evidence)
        if idempotency_key in seen_idempotency_keys:
            raise ValueError(f"model action duplicates idempotency_key: {idempotency_key}")
        seen_idempotency_keys.add(idempotency_key)
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
        if action.action_type == "defer":
            downgraded = sorted(
                feedback_id
                for feedback_id in action.evidence.feedback_ids
                if deterministic_action_type_by_feedback_id.get(feedback_id)
                in {"no_action", "link_to_upload"}
            )
            if downgraded:
                raise ValueError(
                    "model action defers feedback already classified as non-mutating: "
                    f"{downgraded}"
                )
        covered_feedback_ids.update(action.evidence.feedback_ids)
        proposed_actions.append(
            ProposedAction(
                action_id=f"act_model_{index}",
                action_type=action.action_type,
                classification=action.classification,
                idempotency_key=idempotency_key,
                evidence=action.evidence,
                target_repo=action.target_repo,
                validation="accepted",
                execution="not_executed",
            )
        )

    missing_feedback_ids = sorted(allowed_feedback_ids - covered_feedback_ids)
    if missing_feedback_ids:
        raise ValueError(f"model actions do not cover included feedback_ids: {missing_feedback_ids}")
    return proposed_actions


def _deterministic_action_types(base_plan: FeedbackPlan) -> dict[str, CuratorActionType]:
    result: dict[str, CuratorActionType] = {}
    for action in base_plan.proposed_actions:
        for feedback_id in action.evidence.feedback_ids:
            result[feedback_id] = action.action_type
    return result


def _require_all_object_properties(schema: Any) -> None:
    if isinstance(schema, dict):
        schema.pop("default", None)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            schema["additionalProperties"] = False
            schema["required"] = sorted(properties)
        for value in schema.values():
            _require_all_object_properties(value)
    elif isinstance(schema, list):
        for item in schema:
            _require_all_object_properties(item)


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
