from __future__ import annotations

import json

import pytest

from curator.model_tasks import UploadReviewModelOutput, strict_model_json_schema
from curator.upload_model_eval import (
    DEFAULT_UPLOAD_SCENARIO_FIXTURE,
    build_upload_review_scenario_request,
    load_upload_review_scenario_cases,
    score_upload_review_scenario_output,
)


@pytest.mark.parametrize(
    "case",
    load_upload_review_scenario_cases(DEFAULT_UPLOAD_SCENARIO_FIXTURE),
    ids=lambda case: case.name,
)
def test_upload_review_scenario_expected_outputs_score(case) -> None:
    output = UploadReviewModelOutput(
        schema_version="1",
        upload_id=case.upload_id,
        decision=case.expected.decision,
        files=[
            {
                "path": case.expected.path,
                "content": _content(
                    doc_id=case.expected.path.rsplit("/", maxsplit=1)[-1].removesuffix(".md"),
                    doc_type=case.expected.type,
                    tags=case.expected.required_tags,
                ),
            }
        ],
        policy_patch={
            "allowed_types_add": case.expected.policy_types_add,
            "allowed_tags_add": case.expected.policy_tags_add,
        },
        rationale="Fixture output preserves the upload purpose and proposes minimal policy additions.",
        reason="ready for draft corpus PR",
    )

    result = score_upload_review_scenario_output(case, output)

    assert result.passed, result.failures


def test_upload_review_prompt_carries_policy_agency_advice() -> None:
    case = load_upload_review_scenario_cases(DEFAULT_UPLOAD_SCENARIO_FIXTURE)[0]

    request = build_upload_review_scenario_request(
        case=case,
        model="eval-model",
        max_tokens=1000,
    )

    prompt_input = json.loads(request.input["messages"][1]["content"])
    assert prompt_input["upload_id"] == "upl_eval_dev_environment"
    assert "preference" not in prompt_input["corpus_policy"]["allowed_types"]
    assert "uv" not in prompt_input["corpus_policy"]["allowed_tags"]
    assert any("propose a small policy_patch" in item for item in prompt_input["constraints"])
    assert any("type preference under the preferences root" in item for item in prompt_input["constraints"])
    assert any("uv and mise" in item for item in prompt_input["constraints"])
    assert any("Do not drop a central concrete tag" in item for item in prompt_input["constraints"])
    assert any("delimiter lines are exactly three hyphens" in item for item in prompt_input["constraints"])
    assert any("only id, type, tags, aliases, and related" in item for item in prompt_input["constraints"])


def test_upload_review_response_schema_is_strict_json_schema() -> None:
    schema = strict_model_json_schema(UploadReviewModelOutput)

    assert set(schema["required"]) == {
        "schema_version",
        "upload_id",
        "decision",
        "files",
        "policy_patch",
        "rationale",
        "reason",
    }
    assert set(schema["$defs"]["UploadReviewDraftFile"]["required"]) == {"path", "content"}
    assert set(schema["$defs"]["UploadReviewPolicyPatch"]["required"]) == {
        "allowed_types_add",
        "allowed_tags_add",
    }


def _content(*, doc_id: str, doc_type: str, tags: list[str]) -> str:
    return (
        "---\n"
        f"id: {doc_id}\n"
        f"type: {doc_type}\n"
        f"tags: [{', '.join(tags)}]\n"
        "---\n\n"
        "# Normalized Upload\n\n"
        "This review draft preserves the useful owner-specific context.\n"
    )
