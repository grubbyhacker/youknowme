from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from curator.model_tasks import UploadReviewModelOutput, strict_model_json_schema
from curator.models import ModelCallRequest
from curator.upload_draft import ALLOWED_TAGS, ALLOWED_TYPES
from ykm.build import parse_frontmatter


DEFAULT_UPLOAD_SCENARIO_FIXTURE = Path("fixtures/curator/model-upload-review/scenarios.json")


class UploadReviewInputFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str


class ExpectedUploadReviewOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    path: str
    type: str
    required_tags: list[str] = Field(default_factory=list)
    policy_roots_add: list[str] = Field(default_factory=list)
    policy_types_add: list[str] = Field(default_factory=list)
    policy_tags_add: list[str] = Field(default_factory=list)


class UploadReviewScenarioCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    upload_id: str
    manifest: dict[str, Any]
    files: list[UploadReviewInputFile]
    expected: ExpectedUploadReviewOutcome


class UploadReviewScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: str
    passed: bool
    failures: list[str] = Field(default_factory=list)


def load_upload_review_scenario_cases(
    path: Path = DEFAULT_UPLOAD_SCENARIO_FIXTURE,
    *,
    names: set[str] | None = None,
) -> list[UploadReviewScenarioCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [UploadReviewScenarioCase.model_validate(case) for case in payload["cases"]]
    if names is not None:
        cases = [case for case in cases if case.name in names]
    return cases


def build_upload_review_scenario_request(
    *,
    case: UploadReviewScenarioCase,
    model: str,
    max_tokens: int,
    run_id_prefix: str = "eval-upload",
) -> ModelCallRequest:
    run_id = f"{run_id_prefix}-{case.name}"
    prompt_input = {
        "schema_version": "1",
        "run_id": run_id,
        "upload_id": case.upload_id,
        "manifest": case.manifest,
        "files": [file.model_dump(mode="json") for file in case.files],
        "corpus_policy": {
            "allowed_types": sorted(ALLOWED_TYPES),
            "allowed_tags": sorted(ALLOWED_TAGS),
            "corpus_roots": [
                "homemaint",
                "preferences",
                "skills",
                "substack",
                "workhistory",
                "writingsamples",
            ],
        },
        "constraints": [
            "Return only valid JSON matching the response schema.",
            "Produce reviewable corpus markdown files; do not merge or publish anything.",
            "Every output file must be complete markdown with a frontmatter block whose delimiter lines are exactly three hyphens: ---.",
            "Output frontmatter may contain only id, type, tags, aliases, and related; choose the corpus root through the file path, not a root frontmatter field.",
            "Treat corpus policy as a consistency guardrail and review surface, not as an immutable permission boundary.",
            "Prefer existing corpus types and tags when they fit.",
            "When existing vocabulary does not fit, propose a small policy_patch instead of misclassifying the document.",
            "Use policy_patch.corpus_roots_add, allowed_types_add, and allowed_tags_add for new vocabulary needed by the draft.",
            "Every frontmatter type must already be in corpus_policy.allowed_types or be listed in policy_patch.allowed_types_add.",
            "Every frontmatter tag must already be in corpus_policy.allowed_tags or be listed in policy_patch.allowed_tags_add.",
            "Every output path must start with an existing corpus_policy.corpus_roots value or one listed in policy_patch.corpus_roots_add.",
            "Do not invent related IDs. Only include related when the upload itself names an exact existing corpus id; otherwise mention the relationship in prose instead.",
            "A review PR is the owner permission request for bounded corpus policy additions; prefer an integrated draft with a minimal policy_patch over needs_owner_action when the change can be reviewed as code.",
            "Use decision needs_owner_action only when the upload lacks enough context, has unresolved safety concerns, or cannot be turned into a small reviewable corpus-policy and markdown diff.",
            "Choose corpus roots semantically. Use `dev/` for development environment, personal production infrastructure, software operations, and service runbooks; do not place that material under `preferences/` merely because it describes Roger.",
            "Use `preferences/` only for stable preferences, defaults, tastes, and communication style.",
            "If using a corpus root that is absent from corpus_policy.corpus_roots, add it to policy_patch.corpus_roots_add.",
            "Preserve central concrete tool, product, or technology names as tags when they are likely retrieval handles for user questions, such as uv and mise in a Python tooling document.",
            "Do not drop a central concrete tag merely because it appears in the body; add it to policy_patch.allowed_tags_add when absent from policy.",
            "Do not preserve an invalid uploaded type when a better new type is clear from the document purpose.",
            "Do not weaken validation limits, remove existing policy values, or include secrets.",
            "Use decision integrated only when files contain normalized corpus markdown for review.",
            "Use decision needs_owner_action when the upload cannot be safely normalized from the supplied context.",
            "Set content_summary to one short sentence that identifies the uploaded document for a PR reviewer without copying intake excerpts.",
            "Keep rationale short and state why any policy additions are needed.",
        ],
    }
    return ModelCallRequest(
        task_name="upload_review",
        run_id=run_id,
        model=model,
        input={
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the YouKnowMe Curator upload-review model. Normalize staged "
                        "markdown into reviewable corpus files and propose minimal policy additions "
                        "when the current corpus vocabulary is missing needed terms."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_input, sort_keys=True),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "upload_review_output",
                    "schema": strict_model_json_schema(UploadReviewModelOutput),
                    "strict": True,
                },
            },
            "temperature": 0,
            "metadata": {"feature": "upload_review"},
        },
        max_tokens=max_tokens,
    )


def score_upload_review_scenario_output(
    case: UploadReviewScenarioCase,
    output: UploadReviewModelOutput,
) -> UploadReviewScenarioResult:
    failures: list[str] = []
    expected = case.expected
    if output.upload_id != case.upload_id:
        failures.append(f"upload_id expected {case.upload_id}, got {output.upload_id}")
    if output.decision != expected.decision:
        failures.append(f"decision expected {expected.decision}, got {output.decision}")

    matching_files = [file for file in output.files if file.path == expected.path]
    if not matching_files:
        failures.append(f"missing expected file path {expected.path}")
    elif len(matching_files) > 1:
        failures.append(f"multiple files for expected path {expected.path}")
    else:
        metadata, body = parse_frontmatter(matching_files[0].content)
        if not body.strip():
            failures.append(f"{expected.path}: empty body")
        if metadata.get("type") != expected.type:
            failures.append(f"{expected.path}: type expected {expected.type}, got {metadata.get('type')}")
        extra_fields = sorted(set(metadata) - {"id", "type", "tags", "aliases", "related"})
        if extra_fields:
            failures.append(f"{expected.path}: unsupported frontmatter fields {extra_fields}")
        tags = metadata.get("tags")
        tag_set = set(tags) if isinstance(tags, list) else set()
        missing_tags = sorted(set(expected.required_tags) - tag_set)
        if missing_tags:
            failures.append(f"{expected.path}: missing tags {missing_tags}")

    policy_roots = set(output.policy_patch.corpus_roots_add)
    missing_roots = sorted(set(expected.policy_roots_add) - policy_roots)
    if missing_roots:
        failures.append(f"policy_patch.corpus_roots_add missing {missing_roots}")
    policy_types = set(output.policy_patch.allowed_types_add)
    missing_types = sorted(set(expected.policy_types_add) - policy_types)
    if missing_types:
        failures.append(f"policy_patch.allowed_types_add missing {missing_types}")
    policy_tags = set(output.policy_patch.allowed_tags_add)
    missing_policy_tags = sorted(set(expected.policy_tags_add) - policy_tags)
    if missing_policy_tags:
        failures.append(f"policy_patch.allowed_tags_add missing {missing_policy_tags}")
    return UploadReviewScenarioResult(case=case.name, passed=not failures, failures=failures)
