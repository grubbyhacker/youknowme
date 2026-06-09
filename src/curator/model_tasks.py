from __future__ import annotations

from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from curator.models import (
    CURATOR_SCHEMA_VERSION,
    CuratorPrState,
    ModelCallResponse,
    ProposedAction,
    UploadDecision,
)


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
