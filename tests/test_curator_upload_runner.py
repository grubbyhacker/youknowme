from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


from curator.adapters import (
    FixtureBrokerAdapter,
)
from curator.models import (
    ExecutionIntent,
    ExecutionResult,
    UploadQueueSnapshot,
    UploadPlan,
)
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run
from curator.runner import (
    _execute_agentic_upload_review_prs,
    _target_repo_from_task_payload,
    parse_curator_task_payload,
)



from tests.curator_test_support import (
    _fake_corpus_checkout,
    _fake_git,
    _fake_mise,
    _upload_agent_preview_and_bundle,
)


def test_runner_agentic_upload_review_respects_upload_mutation_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    preview_one, bundle_one = _upload_agent_preview_and_bundle(tmp_path)
    preview_two = preview_one.model_copy(
        update={
            "upload_id": "upl_second",
            "action_id": "upl_act_2",
            "idempotency_key": "upload:test-agentic-upload-second",
            "branch": "curator/run-upload-agent/upload-upl-second-test",
        }
    )
    bundle_two = bundle_one.model_copy(update={"upload_id": "upl_second"})
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "allowed_operations": ["pull.create"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_execute(**kwargs):
        captured["upload_ids"] = [preview.upload_id for preview in kwargs["previews"]]
        captured["target_repo"] = kwargs["target_repo"]
        return [], []

    monkeypatch.setattr("curator.runner.execute_agentic_upload_review_prs_in_checkout", fake_execute)

    _execute_agentic_upload_review_prs(
        config=CuratorDryRunConfig(
            run_id="run-upload-agent",
            intake=tmp_path / "intake",
            output=tmp_path / "output",
            broker_fixture=broker_fixture,
        ),
        run_id="run-upload-agent",
        task_payload=None,
        upload_plan=UploadPlan(
            run_id="run-upload-agent",
            included_upload_ids=["upl_tooling", "upl_second"],
            review_previews=[preview_one, preview_two],
            created_at=datetime.fromisoformat("2026-06-11T00:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(
            counts={"pending": 2},
            pending_uploads=["upl_tooling", "upl_second"],
            bundles=[bundle_one, bundle_two],
        ),
        target_repo="grubbyhacker/ykmcorpus-staging",
        model="ykm-codex-gpt-5-mini",
        max_attempts=2,
        max_upload_prs=1,
        validation_command=["mise", "run", "validate"],
    )

    assert captured["upload_ids"] == ["upl_tooling"]
    assert captured["target_repo"] == "grubbyhacker/ykmcorpus-staging"


def test_runner_uses_broker_task_repo_as_corpus_target() -> None:
    assert (
        _target_repo_from_task_payload({"repo": "grubbyhacker/ykmcorpus-staging"})
        == "grubbyhacker/ykmcorpus-staging"
    )


def test_broker_task_payload_preserves_staging_repo_metadata() -> None:
    task_payload, task, message = parse_curator_task_payload(
        {
            "run_id": "run-staging",
            "repo": "grubbyhacker/ykmcorpus-staging",
            "base_branch": "main",
            "branch": "curator-staging/ykm-curator/run-staging",
            "worker_agent_id": "ykm-curator:run-staging",
            "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus-staging.git",
            "task": json.dumps(
                {
                    "schema_version": "1",
                    "run_id": "$SANDBOX_RUN_ID",
                    "mode": "dry_run",
                    "enabled_actions": ["plan_uploads"],
                }
            ),
        }
    )

    assert task is not None
    assert message == "broker task contract loaded with embedded Curator task"
    assert task.run_id == "run-staging"
    assert task_payload["repo"] == "grubbyhacker/ykmcorpus-staging"
    assert (
        task_payload["broker_remote_url"]
        == "http://broker:8080/git/grubbyhacker/ykmcorpus-staging.git"
    )


def test_runner_observes_model_upload_review_draft_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_tooling"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_tooling"}) + "\n",
        encoding="utf-8",
    )
    (files / "tooling.md").write_text(
        "---\nid: tooling\ntype: preference\ntags: [python, uv]\n---\n\n# Tooling\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-upload-model",
                "mode": "dry_run",
                "enabled_actions": ["plan_uploads"],
                "model_upload_review": True,
                "upload_review_model": "anthropic/claude-sonnet-4.6",
                "model_call_budget": {"max_calls_per_run": 1, "max_tokens_per_run": 1000},
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
                "responses": {
                    "upload_review": {
                        "schema_version": "1",
                        "task_name": "upload_review",
                        "output": {
                            "schema_version": "1",
                            "upload_id": "upl_tooling",
                            "decision": "integrated",
                            "files": [
                                {
                                    "path": "preferences/tooling.md",
                                    "content": (
                                        "---\n"
                                        "id: tooling\n"
                                        "type: preference\n"
                                        "tags: [python, uv]\n"
                                        "---\n\n"
                                        "# Tooling\n"
                                    ),
                                }
                            ],
                            "policy_patch": {
                                "corpus_roots_add": ["preferences"],
                                "allowed_types_add": ["preference"],
                                "allowed_tags_add": ["python", "uv"],
                            },
                            "content_summary": "A tooling note about Python development setup.",
                            "rationale": "The upload can be normalized.",
                            "reason": "ready for validation",
                        },
                        "usage": {"input_tokens": 12, "output_tokens": 8},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    corpus = _fake_corpus_checkout(tmp_path)
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            model_proxy_fixture=model_fixture,
            corpus_checkout=corpus,
        )
    )

    assert report.status == "pass"
    assert report.model_call_count == 1
    assert report.model_token_count == 20
    assert report.upload_review_previews[0]["draft_status"] == "model_review_candidate"
    assert report.upload_review_observation_count == 1
    assert report.upload_review_validation_failure_count == 0
    observation = report.upload_review_observations[0]
    assert observation["status"] == "pass"
    assert observation["draft_paths"] == ["preferences/tooling.md"]
    assert observation["policy_roots_add"] == ["preferences"]
    assert observation["policy_types_add"] == ["preference"]
    assert observation["policy_tags_add"] == ["python", "uv"]
    assert observation["command"] == ["mise", "run", "validate"]
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## Upload Review Observations" in markdown
    assert "`upl_tooling`: `pass`" in markdown


def test_runner_manual_live_creates_upload_review_pr_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_tooling"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_tooling"}) + "\n",
        encoding="utf-8",
    )
    (files / "tooling.md").write_text(
        "---\nid: tooling\ntype: procedure\ntags: [home]\n---\n\n# Tooling\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-upload-pr",
                "mode": "manual_live",
                "enabled_actions": ["plan_uploads"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 2,
                    "upload": 2,
                    "feedback": 0,
                },
                "model_upload_review": True,
                "upload_review_model": "anthropic/claude-sonnet-4.6",
                "model_call_budget": {"max_calls_per_run": 1, "max_tokens_per_run": 1000},
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
                "responses": {
                    "upload_review": {
                        "schema_version": "1",
                        "task_name": "upload_review",
                        "output": {
                            "schema_version": "1",
                            "upload_id": "upl_tooling",
                            "decision": "integrated",
                            "files": [
                                {
                                    "path": "homemaint/tooling.md",
                                    "content": (
                                        "---\n"
                                        "id: tooling\n"
                                        "type: procedure\n"
                                        "tags: [home-maintenance]\n"
                                        "---\n\n"
                                        "# Tooling\n"
                                    ),
                                }
                            ],
                            "policy_patch": {
                                "allowed_types_add": [],
                                "allowed_tags_add": [],
                            },
                            "content_summary": "A tooling note about Python development setup.",
                            "rationale": "The upload can be normalized.",
                            "reason": "ready for validation",
                        },
                    }
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
                "existing_idempotency_keys": [],
                "allowed_operations": ["pull.create"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    _fake_git(tmp_path, monkeypatch)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_url="http://broker:8080",
            broker_fixture=broker_fixture,
            model_proxy_fixture=model_fixture,
            corpus_checkout=_fake_corpus_checkout(tmp_path),
        )
    )

    assert report.status == "pass"
    assert report.upload_review_observation_count == 1
    assert report.execution_intent_count == 1
    assert report.execution_intents[0]["operation"] == "pull.create"
    assert report.execution_intents[0]["title"] == (
        "YouKnowMe Curator upload review: homemaint/tooling.md"
    )
    assert "- Page: `homemaint/tooling.md`" in report.execution_intents[0]["body"]
    assert "- Content: A tooling note about Python development setup." in report.execution_intents[0][
        "body"
    ]
    assert report.simulated_execution_results[0]["status"] == "simulated"
    assert report.simulated_execution_results[0]["branch"].startswith(
        "curator/run-upload-pr/upload-upl-tooling-"
    )
    metadata_path = intake / "uploads" / "claimed" / "upl_tooling" / "curator.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["state"] == "pr_opened"
    assert metadata["decision"] == "integrated"
    assert metadata["run_id"] == "run-upload-pr"
    assert metadata["branch"].startswith("curator/run-upload-pr/upload-upl-tooling-")
    assert report.upload_metadata_update_count == 1
    assert report.upload_metadata_update_paths == [str(metadata_path)]
    assert not pending.exists()
    assert any(probe.name == "manual-live-upload-pr" and probe.status == "pass" for probe in report.probes)


def test_runner_retries_pending_upload_pr_creation_after_broker_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_tooling"
    files = pending / "files"
    files.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_tooling"}) + "\n",
        encoding="utf-8",
    )
    (files / "tooling.md").write_text(
        "---\nid: tooling\ntype: procedure\ntags: [home]\n---\n\n# Tooling\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-upload-pr-retry",
                "mode": "manual_live",
                "enabled_actions": ["plan_uploads"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 2,
                    "upload": 2,
                    "feedback": 0,
                },
                "model_upload_review": True,
                "upload_review_model": "anthropic/claude-sonnet-4.6",
                "model_call_budget": {"max_calls_per_run": 1, "max_tokens_per_run": 1000},
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
                "responses": {
                    "upload_review": {
                        "schema_version": "1",
                        "task_name": "upload_review",
                        "output": {
                            "schema_version": "1",
                            "upload_id": "upl_tooling",
                            "decision": "integrated",
                            "files": [
                                {
                                    "path": "homemaint/tooling.md",
                                    "content": (
                                        "---\n"
                                        "id: tooling\n"
                                        "type: procedure\n"
                                        "tags: [home-maintenance]\n"
                                        "---\n\n"
                                        "# Tooling\n"
                                    ),
                                }
                            ],
                            "policy_patch": {
                                "allowed_types_add": [],
                                "allowed_tags_add": [],
                            },
                            "content_summary": "A tooling note about Python development setup.",
                            "rationale": "The upload can be normalized.",
                            "reason": "ready for validation",
                        },
                    }
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
                "existing_idempotency_keys": [],
                "allowed_operations": ["pull.create"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _fake_mise(tmp_path, monkeypatch, exit_code=0, stdout="validation ok\n")
    _fake_git(tmp_path, monkeypatch)
    monkeypatch.setenv("BROKER_AGENT_ID", "ykm-curator")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def fail_create_pull(self, intent: ExecutionIntent) -> ExecutionResult:
        return ExecutionResult(
            action_id=intent.action_id,
            operation=intent.operation,
            idempotency_key=intent.idempotency_key,
            status="failed",
            target_repo=intent.target_repo,
            branch=intent.branch,
            message="broker unavailable",
        )

    original_create_pull = FixtureBrokerAdapter.create_pull
    monkeypatch.setattr(FixtureBrokerAdapter, "create_pull", fail_create_pull)

    first_report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output-first",
            task=task,
            broker_url="http://broker:8080",
            broker_fixture=broker_fixture,
            model_proxy_fixture=model_fixture,
            corpus_checkout=_fake_corpus_checkout(tmp_path),
        )
    )

    pending_files = list((intake / "upload-pr-creations" / "pending").glob("*.json"))
    assert first_report.status == "fail"
    assert first_report.upload_metadata_update_count == 0
    assert (intake / "uploads" / "pending" / "upl_tooling").exists()
    assert len(pending_files) == 1
    pending_intent = json.loads(pending_files[0].read_text(encoding="utf-8"))
    assert pending_intent["operation"] == "pull.create"
    assert pending_intent["branch"].startswith("curator/run-upload-pr-retry/upload-upl-tooling-")

    monkeypatch.setattr(FixtureBrokerAdapter, "create_pull", original_create_pull)
    retry_task = tmp_path / "retry-task.json"
    retry_task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-upload-pr-retry-2",
                "mode": "manual_live",
                "enabled_actions": ["plan_uploads"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 2,
                    "upload": 2,
                    "feedback": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    second_report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output-second",
            task=retry_task,
            broker_url="http://broker:8080",
            broker_fixture=broker_fixture,
            corpus_checkout=_fake_corpus_checkout(tmp_path / "retry"),
        )
    )

    assert second_report.status == "pass"
    retried_results = [
        result
        for result in second_report.simulated_execution_results
        if result["operation"] == "pull.create"
    ]
    assert len(retried_results) == 1
    assert retried_results[0]["status"] == "simulated"
    metadata_path = intake / "uploads" / "claimed" / "upl_tooling" / "curator.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["state"] == "pr_opened"
    assert metadata["decision"] == "integrated"
    assert metadata["run_id"] == "run-upload-pr-retry-2"
    assert metadata["branch"].startswith("curator/run-upload-pr-retry/upload-upl-tooling-")
    assert second_report.upload_metadata_update_count == 1
    assert not pending_files[0].exists()
