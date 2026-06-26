from __future__ import annotations

import json
from pathlib import Path


from curator.markers import parse_curator_markers
from curator.models import (
    ExecutionResult,
    PrRepairResult,
)
from curator.upload_observe import (
    UploadReviewObservation,
)
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_invalid_task_enabled_action_fails_before_planning(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-invalid-task",
                "mode": "dry_run",
                "enabled_actions": ["plan_everything"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 0,
                    "upload": 0,
                    "feedback": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="fallback", intake=intake, output=output, task=task)
    )

    assert report.status == "fail"
    assert report.included_feedback_ids == []
    assert report.partial_failures[0]["name"] == "task"
    assert next(probe for probe in report.probes if probe.name == "task").status == "fail"


def test_manual_live_mode_is_explicitly_guarded_until_adapters_exist(
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
                "run_id": "run-live",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 1,
                    "upload": 0,
                    "feedback": 1,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )

    assert report.status == "fail"
    assert report.github_mutation_count == 0
    assert report.model_call_count == 0
    assert {failure["name"] for failure in report.partial_failures} == {
        "agentic-feedback",
        "broker",
        "manual-live-feedback",
    }
    assert report.policy_decisions[0]["status"] == "allowed"
    assert report.execution_intent_count == 1
    assert report.execution_intents[0]["operation"] == "issue.create"
    assert report.execution_intents[0]["execution"] == "not_executed"
    assert report.execution_intents[0]["title"].startswith("YouKnowMe Curator corpus_issue")
    assert report.execution_intents[0]["labels"] == [
        "ykm-curator",
        "feedback",
        "corpus",
    ]
    assert report.execution_intents[0]["assignees"] == []
    issue_markers = parse_curator_markers(report.execution_intents[0]["body"])
    assert issue_markers.run_id == "run-live"
    assert issue_markers.feedback_ids == ["fb_1"]
    assert len(report.feedback_plan_paths) == 2
    assert len(report.upload_plan_paths) == 2
    assert (intake / "feedback" / "runs" / "run-live" / "feedback-plan.json").exists()
    assert (intake / "uploads" / "runs" / "run-live" / "upload-plan.json").exists()
    assert not (intake / "feedback" / "curator-state.json").exists()
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "labels: `ykm-curator`, `feedback`, `corpus`" in markdown


def test_manual_live_noop_actions_do_not_require_broker(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"positive_content"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-live-noop",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 0,
                    "upload": 0,
                    "feedback": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("BROKER_AGENT_ID", raising=False)
    monkeypatch.delenv("BROKER_AGENT_SECRET", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )

    assert report.status == "fail"
    assert report.policy_decisions[0]["action_type"] == "corpus_issue"
    assert next(probe for probe in report.probes if probe.name == "broker").status == "fail"
    assert {failure["name"] for failure in report.partial_failures} == {
        "broker",
        "execution-policy",
        "manual-live",
        "manual-live-feedback",
    }


def test_manual_live_upload_previews_require_broker_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_live_upload"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_live_upload"}) + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-live-upload",
                "mode": "manual_live",
                "enabled_actions": ["plan_uploads"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 1,
                    "upload": 1,
                    "feedback": 0,
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
        )
    )

    assert report.status == "fail"
    assert report.upload_review_preview_count == 1
    assert {failure["name"] for failure in report.partial_failures} == {"manual-live", "broker"}
    assert next(probe for probe in report.probes if probe.name == "broker").status == "fail"
    assert report.github_mutation_count == 0
    assert pending.exists()


def test_manual_live_policy_preflight_reports_budget_denial(
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
                "run_id": "run-live-denied",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 0,
                    "upload": 0,
                    "feedback": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )

    assert report.status == "fail"
    assert report.policy_denial_count == 1
    assert report.policy_decisions[0]["status"] == "denied"
    assert report.execution_intent_count == 0
    assert report.github_mutation_count == 0


def test_manual_live_model_budget_requires_model_proxy_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"positive_content"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-live-model",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 0,
                    "upload": 0,
                    "feedback": 0,
                },
                "model_call_budget": {
                    "max_calls_per_run": 1,
                    "max_tokens_per_run": 1000,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )

    assert report.status == "fail"
    assert report.model_call_budget == {"max_calls_per_run": 1, "max_tokens_per_run": 1000}
    assert report.model_call_count == 0
    assert {failure["name"] for failure in report.partial_failures} == {
        "broker",
        "execution-policy",
        "manual-live",
        "manual-live-feedback",
        "model-proxy",
    }


def test_manual_live_uses_broker_and_model_fixtures_for_offline_preflight(
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
                "run_id": "run-live-fixtures",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 1,
                    "upload": 0,
                    "feedback": 1,
                },
                "model_call_budget": {
                    "max_calls_per_run": 1,
                    "max_tokens_per_run": 1000,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "existing_branches": [],
                "allowed_operations": ["issue.create", "pull.create"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    model_fixture = tmp_path / "model-fixture.json"
    model_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 1000,
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
            broker_fixture=broker_fixture,
            model_proxy_fixture=model_fixture,
        )
    )

    assert report.status == "fail"
    assert {failure["name"] for failure in report.partial_failures} == {
        "agentic-feedback",
        "manual-live-feedback",
    }
    assert next(probe for probe in report.probes if probe.name == "broker").status == "pass"
    assert next(probe for probe in report.probes if probe.name == "model-proxy").status == "pass"
    assert next(probe for probe in report.probes if probe.name == "broker-preflight").status == "pass"
    assert report.execution_intent_count == 1


def test_manual_live_feedback_issue_fixture_advances_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    line = (
        '{"event":"feedback","feedback_id":"fb_1",'
        '"comment":"This was not useful enough to act on."}\n'
    )
    feedback.write_text(line, encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-live-feedback-fixture",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 1,
                    "upload": 0,
                    "feedback": 1,
                },
                "feedback_executor": "codex_proxy",
                "feedback_agent_max_attempts": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "existing_branches": [],
                "allowed_operations": ["issue.create", "pull.create"],
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
            broker_fixture=broker_fixture,
        )
    )

    decisions = [
        json.loads(line)
        for line in (intake / "feedback" / "curator-decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    state = json.loads((intake / "feedback" / "curator-state.json").read_text(encoding="utf-8"))

    assert report.status == "pass"
    assert report.checkpoint_advanced is True
    assert report.feedback_decisions_appended == 1
    assert decisions[0]["decision"] == "issue_opened"
    assert decisions[0]["feedback_id"] == "fb_1"
    assert state["feedback_checkpoint"]["byte_offset"] == len(line)


def test_manual_live_combined_profile_processes_all_enabled_actions(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback_line = (
        '{"event":"feedback","feedback_id":"fb_live_1",'
        '"comment":"This answer should become a durable follow-up issue."}\n'
    )
    feedback.write_text(feedback_line, encoding="utf-8")
    pending = intake / "uploads" / "pending" / "upl_live_1"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_live_1"}) + "\n",
        encoding="utf-8",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "allowed_operations": [
                    "issue.create",
                    "issue.comment",
                    "issue.label.add",
                    "issue.label.remove",
                    "pull.create",
                    "pull.review.dismiss",
                    "pull.review_thread.resolve",
                ],
                "pr_snapshots": [
                    {
                        "number": 18,
                        "state": "open",
                        "body": (
                            "YKM-Curator-Run: prior-live-run\n"
                            "YKM-Curator-Upload: upl_prior\n"
                        ),
                        "branch": "curator/prior-live-run/upload-upl-prior",
                        "labels": ["ym-curator: needs work"],
                        "review_decision": "changes_requested",
                        "checks_conclusion": "failure",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    model_proxy_fixture = tmp_path / "model-proxy-fixture.json"
    model_proxy_fixture.write_text(
        json.dumps({"schema_version": "1", "reachable": True}) + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-live-combined",
                "mode": "manual_live",
                "enabled_actions": [
                    "reconcile",
                    "plan_feedback",
                    "plan_uploads",
                    "repair_prs",
                ],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 3,
                    "upload": 1,
                    "feedback": 2,
                },
                "feedback_executor": "codex_proxy",
                "upload_review_executor": "codex_proxy",
                "pr_repair_executor": "codex_proxy",
                "feedback_agent_model": "ykm-codex-gpt-5-mini",
                "upload_review_agent_model": "ykm-codex-gpt-5-mini",
                "pr_repair_model": "ykm-codex-gpt-5-mini",
                "feedback_agent_max_attempts": 2,
                "upload_review_max_attempts": 2,
                "pr_repair_max_per_run": 1,
                "feedback_agent_validation_command": ["mise", "run", "validate"],
                "upload_review_validation_command": ["mise", "run", "validate"],
                "pr_repair_validation_command": ["mise", "run", "validate"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_feedback_actions(**kwargs):
        captured["feedback_executor"] = kwargs
        [intent] = kwargs["intents"]
        return [
            ExecutionResult(
                action_id=intent.action_id,
                operation="issue.create",
                idempotency_key=intent.idempotency_key,
                status="executed",
                target_repo=intent.target_repo,
                issue_number=41,
                message="feedback issue opened",
            )
        ]

    def fake_upload_prs(**kwargs):
        captured["upload_executor"] = kwargs
        [preview] = kwargs["upload_plan"].review_previews
        return (
            [
                ExecutionResult(
                    action_id=preview.action_id,
                    operation="pull.create",
                    idempotency_key=preview.idempotency_key,
                    status="executed",
                    target_repo="grubbyhacker/ykmcorpus",
                    branch=preview.branch,
                    pr_number=42,
                    message="upload PR opened",
                )
            ],
            [
                UploadReviewObservation(
                    upload_id=preview.upload_id,
                    action_id=preview.action_id,
                    status="pass",
                    decision="integrated",
                    message="upload review passed",
                    executor="codex_proxy",
                    model=kwargs["model"],
                    attempts=1,
                )
            ],
        )

    def fake_pr_repairs(**kwargs):
        captured["pr_repair_executor"] = kwargs
        repairable = [
            reconciliation
            for reconciliation in kwargs["reconciliations"]
            if reconciliation.pr_state == "changes_requested"
        ]
        assert [reconciliation.pr_number for reconciliation in repairable] == [18]
        return [
            PrRepairResult(
                pr_number=18,
                branch="curator/prior-live-run/upload-upl-prior",
                pr_state="changes_requested",
                executor="codex_proxy",
                model=kwargs["model"],
                status="pushed",
                message="repair pushed",
                changed_files=["homemaint/manual.md"],
                repair_head_sha="repair-sha",
                validation_command=kwargs["validation_command"],
                validation_returncode=0,
                review_request_comment="Curator repair completed; ready for review again.",
                review_request_comment_status="pending",
                pushed=True,
            )
        ]

    monkeypatch.setattr("curator.runner.execute_agentic_feedback_actions", fake_feedback_actions)
    monkeypatch.setattr("curator.runner._execute_agentic_upload_review_prs", fake_upload_prs)
    monkeypatch.setattr("curator.runner.execute_pr_repairs", fake_pr_repairs)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_fixture=broker_fixture,
            model_proxy_fixture=model_proxy_fixture,
            codex_proxy_base_url="http://proxy:8092/v1",
            codex_proxy_token="proxy-token",
        )
    )

    decisions = [
        json.loads(line)
        for line in (intake / "feedback" / "curator-decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    metadata = json.loads(
        (intake / "uploads" / "claimed" / "upl_live_1" / "curator.json").read_text(
            encoding="utf-8"
        )
    )

    assert report.status == "pass"
    assert report.enabled_actions == ["plan_feedback", "plan_uploads", "reconcile", "repair_prs"]
    assert report.reconciliation["pr_reconciliation_count"] == 1
    assert report.reconciliation["pr_state_counts"] == {"changes_requested": 1}
    assert report.feedback_decisions_appended == 1
    assert decisions[0]["decision"] == "issue_opened"
    assert decisions[0]["feedback_id"] == "fb_live_1"
    assert report.upload_review_preview_count == 1
    assert report.upload_review_observation_count == 1
    assert report.upload_metadata_update_count == 1
    assert metadata["state"] == "pr_opened"
    assert metadata["pr_number"] == 42
    assert report.pr_repair_result_count == 1
    assert report.pr_repair_results[0]["pr_number"] == 18
    assert report.pr_repair_results[0]["status"] == "pushed"
    assert report.github_mutation_count == 3
    assert report.validation_failure_count == 0
    assert any(probe.name == "model-proxy" and probe.status == "pass" for probe in report.probes)
    assert captured["feedback_executor"]["model"] == "ykm-codex-gpt-5-mini"
    assert captured["feedback_executor"]["max_attempts"] == 2
    assert captured["upload_executor"]["model"] == "ykm-codex-gpt-5-mini"
    assert captured["upload_executor"]["max_attempts"] == 2
    assert captured["upload_executor"]["max_upload_prs"] == 1
    assert captured["pr_repair_executor"]["executor"] == "codex_proxy"
    assert captured["pr_repair_executor"]["max_repairs"] == 1
    assert captured["pr_repair_executor"]["validation_command"] == ["mise", "run", "validate"]


def test_manual_live_model_fixture_budget_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"positive_content"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-live-model-budget",
                "mode": "manual_live",
                "enabled_actions": ["plan_feedback"],
                "model_call_budget": {
                    "max_calls_per_run": 2,
                    "max_tokens_per_run": 100,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    model_fixture = tmp_path / "model-fixture.json"
    model_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "max_calls_per_run": 1,
                "max_tokens_per_run": 50,
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
            model_proxy_fixture=model_fixture,
        )
    )

    assert report.status == "fail"
    assert report.model_call_count == 0
    assert report.model_budget_exhausted is True
    assert {failure["name"] for failure in report.partial_failures} == {
        "broker",
        "execution-policy",
        "manual-live",
        "manual-live-feedback",
        "model-budget",
    }
    budget_probe = next(probe for probe in report.probes if probe.name == "model-budget")
    assert budget_probe.details["max_calls_per_run"] == {"requested": 2, "available": 1}
    assert budget_probe.details["max_tokens_per_run"] == {"requested": 100, "available": 50}
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "- Model tokens: `0`" in markdown
    assert "- Model budget exhausted: `True`" in markdown


