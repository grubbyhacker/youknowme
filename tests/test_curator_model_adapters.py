from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from curator.adapters import (
    FixtureModelAdapter,
    HttpModelProxyAdapter,
)
from curator.model_tasks import (
    FeedbackPlanningModelOutput,
    PrBodyDraftModelOutput,
    PrCommentClassificationModelOutput,
    UploadReviewModelOutput,
    validate_model_response_output,
)
from curator.models import (
    ActionEvidence,
    ModelCallRequest,
    ModelCallResponse,
    ModelCallBudget,
)
from curator.pr_repair import (
    _codex_config,
)
from curator.state import deterministic_idempotency_key
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_model_fixture_adapter_returns_typed_response(tmp_path: Path) -> None:
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "feedback_plan",
                        "output": {"accepted": True},
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    response = FixtureModelAdapter.from_path(fixture).call(
        ModelCallRequest(task_name="feedback_plan", input={"feedback_ids": ["fb_1"]})
    )

    assert response.task_name == "feedback_plan"
    assert response.output == {"accepted": True}
    assert response.usage.input_tokens == 10


def test_model_fixture_adapter_rejects_mismatched_response_key(tmp_path: Path) -> None:
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "upload_review",
                        "output": {},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="does not match task_name"):
        FixtureModelAdapter.from_path(fixture)


def test_runner_reports_invalid_model_fixture_contract(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-invalid-model-fixture",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "model_call_budget": {
                    "max_calls_per_run": 1,
                    "max_tokens_per_run": 100,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "upload_review",
                        "output": {},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            model_proxy_fixture=fixture,
        )
    )

    assert report.status == "fail"
    assert any(failure["name"] == "model-proxy" for failure in report.partial_failures)
    assert any(failure["name"] == "model-budget" for failure in report.partial_failures)
    assert report.model_budget_exhausted is True


def test_model_fixture_adapter_validates_typed_response_output(tmp_path: Path) -> None:
    evidence = ActionEvidence(feedback_ids=["fb_1"], source_ids=["src_1"])
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "feedback_plan",
                        "output": {
                            "schema_version": "1",
                            "proposed_actions": [
                                {
                                    "action_type": "corpus_pr",
                                    "classification": "corpus_candidate",
                                    "evidence": evidence.model_dump(),
                                    "target_repo": "grubbyhacker/ykmcorpus",
                                }
                            ],
                        },
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output = FixtureModelAdapter.from_path(fixture).call_typed(
        ModelCallRequest(task_name="feedback_plan", input={"feedback_ids": ["fb_1"]}),
        FeedbackPlanningModelOutput,
    )

    assert output.proposed_actions[0].action_type == "corpus_pr"
    assert output.proposed_actions[0].evidence.source_ids == ["src_1"]


def test_model_fixture_adapter_rejects_invalid_typed_response_output(tmp_path: Path) -> None:
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "feedback_plan",
                        "output": {"schema_version": "1", "proposed_actions": [{"bad": True}]},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        FixtureModelAdapter.from_path(fixture).call_typed(
            ModelCallRequest(task_name="feedback_plan"),
            FeedbackPlanningModelOutput,
        )


def test_runner_uses_model_feedback_planning_when_task_opts_in(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        (
            '{"event":"feedback","feedback_id":"fb_1","category":"new_source",'
            '"source_id":"src_1"}\n'
        ),
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-model-plan",
                "mode": "dry_run",
                "enabled_actions": ["plan_feedback"],
                "model_feedback_planning": True,
                "feedback_model": "deepseek/deepseek-v4-flash",
                "model_call_budget": {
                    "max_calls_per_run": 1,
                    "max_tokens_per_run": 100,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = ActionEvidence(feedback_ids=["fb_1"], source_ids=["src_1"])
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "feedback_plan",
                        "output": {
                            "schema_version": "1",
                            "proposed_actions": [
                                    {
                                        "action_type": "corpus_pr",
                                        "classification": "corpus_candidate",
                                        "evidence": evidence.model_dump(),
                                        "target_repo": "grubbyhacker/ykmcorpus",
                                    }
                            ],
                        },
                        "usage": {"input_tokens": 21, "output_tokens": 9},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            model_proxy_fixture=fixture,
            required_model_proxy=True,
        )
    )

    assert report.status == "pass"
    assert report.model_call_count == 1
    assert report.model_token_count == 30
    assert report.proposed_action_count == 1
    assert report.proposed_actions[0]["action_id"] == "act_model_1"
    assert report.proposed_actions[0]["idempotency_key"] == deterministic_idempotency_key(
        "corpus_pr", evidence
    )
    assert report.proposed_actions[0]["classification"] == "corpus_candidate"
    probe = next(probe for probe in report.probes if probe.name == "model-feedback-planning")
    assert probe.status == "pass"
    assert probe.details["model"] == "deepseek/deepseek-v4-flash"


def test_runner_fails_closed_when_model_plan_cites_unknown_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"needs_owner_action"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-model-invalid",
                "mode": "dry_run",
                "enabled_actions": ["plan_feedback"],
                "model_feedback_planning": True,
                "feedback_model": "deepseek/deepseek-v4-flash",
                "model_call_budget": {
                    "max_calls_per_run": 1,
                    "max_tokens_per_run": 100,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = ActionEvidence(feedback_ids=["fb_2"])
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {
                    "feedback_plan": {
                        "schema_version": "1",
                        "task_name": "feedback_plan",
                        "output": {
                            "schema_version": "1",
                            "proposed_actions": [
                                    {
                                    "action_type": "corpus_issue",
                                    "classification": "corpus_issue",
                                    "evidence": evidence.model_dump(),
                                    "target_repo": "grubbyhacker/ykmcorpus",
                                    }
                            ],
                        },
                        "usage": {"input_tokens": 13, "output_tokens": 8},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            model_proxy_fixture=fixture,
            required_model_proxy=True,
        )
    )

    assert report.status == "fail"
    assert report.model_call_count == 1
    assert report.model_token_count == 21
    assert report.proposed_actions[0]["evidence"]["feedback_ids"] == ["fb_1"]
    probe = next(probe for probe in report.probes if probe.name == "model-feedback-planning")
    assert probe.status == "fail"
    assert "unknown feedback_ids" in probe.message


def test_runner_ignores_model_fixture_when_model_planning_is_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"needs_owner_action"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-no-model-plan",
                "mode": "dry_run",
                "enabled_actions": ["plan_feedback"],
                "model_call_budget": {
                    "max_calls_per_run": 1,
                    "max_tokens_per_run": 100,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
                "responses": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            model_proxy_fixture=fixture,
        )
    )

    assert report.status == "pass"
    assert report.model_call_count == 0
    assert report.proposed_actions[0]["action_id"] == "act_1"
    assert not any(probe.name == "model-feedback-planning" for probe in report.probes)


def test_model_response_output_validates_feedback_plan_actions() -> None:
    evidence = ActionEvidence(feedback_ids=["fb_1"], source_ids=["src_1"])
    response = ModelCallResponse(
        task_name="feedback_plan",
        output={
            "schema_version": "1",
            "proposed_actions": [
                {
                    "action_type": "corpus_pr",
                    "classification": "corpus_candidate",
                    "evidence": evidence.model_dump(),
                    "target_repo": "grubbyhacker/ykmcorpus",
                }
            ],
        },
    )

    output = validate_model_response_output(
        response,
        FeedbackPlanningModelOutput,
        expected_task_name="feedback_plan",
    )

    assert output.proposed_actions[0].action_type == "corpus_pr"
    assert output.proposed_actions[0].evidence.source_ids == ["src_1"]


def test_model_response_output_rejects_invalid_feedback_plan_action() -> None:
    response = ModelCallResponse(
        task_name="feedback_plan",
        output={
            "schema_version": "1",
            "proposed_actions": [
                {
                    "action_id": "act_1",
                    "action_type": "issue",
                    "classification": "owner_action",
                    "idempotency_key": "issue:abc",
                    "evidence": {"feedback_ids": ["fb_1"]},
                    "target_repo": "grubbyhacker/ykmcorpus",
                }
            ],
        },
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate_model_response_output(response, FeedbackPlanningModelOutput)


def test_model_response_output_rejects_task_name_mismatch() -> None:
    response = ModelCallResponse(
        task_name="upload_review",
        output={
            "schema_version": "1",
            "upload_id": "upl_1",
            "decision": "deferred",
            "content_summary": "Upload review needs owner input.",
            "files": [],
            "policy_patch": {"allowed_types_add": [], "allowed_tags_add": []},
            "rationale": "needs owner input",
            "reason": "needs owner input",
        },
    )

    with pytest.raises(ValueError, match="model response task mismatch"):
        validate_model_response_output(
            response,
            UploadReviewModelOutput,
            expected_task_name="feedback_plan",
        )


def test_model_task_output_contracts_validate_pr_and_body_shapes() -> None:
    pr_output = validate_model_response_output(
        ModelCallResponse(
            task_name="pr_comment_classification",
            output={
                "schema_version": "1",
                "pr_number": 44,
                "pr_state": "changes_requested",
                "reason": "review requested concrete changes",
                "needs_owner_input": False,
            },
        ),
        PrCommentClassificationModelOutput,
        expected_task_name="pr_comment_classification",
    )
    body_output = validate_model_response_output(
        ModelCallResponse(
            task_name="pr_body_draft",
            output={
                "schema_version": "1",
                "title": "Curate upload upl_1",
                "body": "Bounded PR body with markers only.",
            },
        ),
        PrBodyDraftModelOutput,
        expected_task_name="pr_body_draft",
    )

    assert pr_output.pr_state == "changes_requested"
    assert body_output.title == "Curate upload upl_1"


def test_model_fixture_adapter_rejects_missing_response(tmp_path: Path) -> None:
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps({"schema_version": "1", "reachable": True, "responses": {}}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(KeyError):
        FixtureModelAdapter.from_path(fixture).call(ModelCallRequest(task_name="missing"))


def test_model_fixture_adapter_fails_closed_when_budget_exceeds_limits(tmp_path: Path) -> None:
    fixture = tmp_path / "model-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 100,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    probe = FixtureModelAdapter.from_path(fixture).budget_probe(
        ModelCallBudget(max_calls_per_run=2, max_tokens_per_run=100)
    )

    assert probe.status == "fail"
    assert probe.details["max_calls_per_run"] == {"requested": 2, "available": 1}


def test_http_model_proxy_adapter_probe_uses_proxy_token_only() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = HttpModelProxyAdapter(
        "http://model-proxy:8080",
        token="proxy-token",
        client=client,
    )

    probe = adapter.probe(required=True)

    assert probe.status == "pass"
    assert len(requests) == 1
    assert str(requests[0].url) == "http://model-proxy:8080/healthz"
    assert requests[0].headers["authorization"] == "Bearer proxy-token"
    assert "openai" not in requests[0].headers
    assert "anthropic" not in requests[0].headers


def test_http_model_proxy_adapter_can_probe_from_call_endpoint_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = HttpModelProxyAdapter(
        "http://gh-agent-proxy:8092/v1/model/call",
        token="proxy-token",
        client=client,
    )

    probe = adapter.probe(required=True)

    assert probe.status == "pass"
    assert str(requests[0].url) == "http://gh-agent-proxy:8092/healthz"


def test_http_model_proxy_adapter_missing_config_does_not_expose_token() -> None:
    adapter = HttpModelProxyAdapter("", token=None)

    probe = adapter.probe(required=True)

    assert probe.status == "fail"
    assert probe.details["missing"] == ["model proxy URL", "model proxy token"]
    assert "proxy-token" not in probe.model_dump_json()


def test_http_model_proxy_adapter_calls_proxy_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": {
                    "schema_version": "1",
                    "proposed_actions": [],
                },
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = HttpModelProxyAdapter(
        "http://gh-agent-proxy:8092/v1/model/call",
        token="proxy-token",
        client=client,
    )

    response = adapter.call(
        ModelCallRequest(
            task_name="feedback_plan",
            run_id="run-model",
            model="deepseek/deepseek-v4-flash",
            input={
                "messages": [{"role": "user", "content": "{}"}],
                "response_format": {"type": "json_object"},
            },
            max_tokens=100,
        )
    )

    assert response.output == {"schema_version": "1", "proposed_actions": []}
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert str(requests[0].url) == "http://gh-agent-proxy:8092/v1/model/call"
    assert requests[0].headers["authorization"] == "Bearer proxy-token"
    request_payload = json.loads(requests[0].content)
    assert request_payload["run_id"] == "run-model"
    assert request_payload["model"] == "deepseek/deepseek-v4-flash"
    assert request_payload["max_tokens"] == 100


def test_runner_uses_http_model_proxy_adapter_for_required_probe(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://model-proxy:8080/healthz"
        assert kwargs["headers"] == {"Authorization": "Bearer proxy-token"}
        return httpx.Response(200)

    monkeypatch.setattr("curator.adapters.httpx.get", fake_get)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-http-model",
            intake=intake,
            output=tmp_path / "output",
            model_proxy_url="http://model-proxy:8080",
            model_proxy_token="proxy-token",
            required_model_proxy=True,
        )
    )

    assert report.status == "pass"
    assert next(probe for probe in report.probes if probe.name == "model-proxy").status == "pass"


def test_codex_proxy_config_uses_responses_wire_api_and_run_header() -> None:
    config = _codex_config(
        model="ykm-codex-haiku",
        proxy_base_url="http://gh-agent-proxy:8092/v1",
    )

    assert 'model = "ykm-codex-haiku"' in config
    assert 'base_url = "http://gh-agent-proxy:8092/v1"' in config
    assert 'wire_api = "responses"' in config
    assert 'sandbox_mode = "danger-full-access"' in config
    assert '"X-GH-Agent-Run-ID" = "YKM_CURATOR_RUN_ID"' in config


