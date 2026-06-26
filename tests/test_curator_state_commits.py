from __future__ import annotations

import json
from pathlib import Path

import httpx

from curator.models import (
    ActionEvidence,
    ProposedAction,
)
from curator.planning import deterministic_branch_name
from curator.state import deterministic_idempotency_key
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_state_only_advances_feedback_checkpoint(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    line = '{"event":"feedback","feedback_id":"fb_1"}\n'
    feedback.write_text(line, encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state",
                "mode": "state_only",
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

    assert report.run_id == "run-state"
    assert report.mode == "state_only"
    assert report.status == "fail"
    assert report.checkpoint_advanced is False
    assert report.feedback_decisions_appended == 0
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert not (intake / "feedback" / "curator-state.json").exists()


def test_state_only_validation_failures_do_not_advance_checkpoint_or_decisions(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    valid_line = '{"event":"feedback","feedback_id":"fb_valid","category":"positive_content"}\n'
    feedback.write_text(valid_line + '{"event":"feedback","feedback_id":\n', encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-invalid",
                "mode": "state_only",
                "enabled_actions": ["plan_feedback"],
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
    assert report.input_error_count == 1
    assert report.included_feedback_ids == ["fb_valid"]
    assert report.checkpoint_advanced is False
    assert report.feedback_decisions_appended == 0
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert not (intake / "feedback" / "curator-state.json").exists()
    assert not (intake / "feedback" / "curator-decisions.jsonl").exists()


def test_state_only_appends_only_noop_link_and_defer_feedback_decisions(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    records = [
        {"event": "feedback", "feedback_id": "fb_positive", "category": "positive_content"},
        {"event": "feedback", "feedback_id": "fb_owner", "category": "needs_owner_action"},
        {
            "event": "feedback",
            "feedback_id": "fb_upload",
            "category": "missing_content",
            "upload_id": "upl_1",
        },
        {
            "event": "feedback",
            "feedback_id": "fb_missing_1",
            "category": "missing_content",
            "source_id": "src_missing_1",
        },
        {"event": "feedback", "feedback_id": "fb_missing_2", "category": "missing_content"},
    ]
    feedback.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-decisions",
                "mode": "state_only",
                "enabled_actions": ["plan_feedback"],
                "github_mutation_budget": {
                    "max_new_objects_per_run": 0,
                    "upload": 0,
                    "feedback": 0,
                },
                "feedback_soft_action_threshold": 2,
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
    assert report.checkpoint_advanced is False
    assert report.feedback_decisions_appended == 0
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert not (intake / "feedback" / "curator-state.json").exists()
    assert not (intake / "feedback" / "curator-decisions.jsonl").exists()
    assert {action["action_type"] for action in report.proposed_actions} == {
        "corpus_issue",
        "corpus_pr",
    }


def test_state_only_broker_fixture_preflight_failure_blocks_state_commits(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    records = [
        {"event": "feedback", "feedback_id": "fb_positive", "category": "positive_content"},
        {
            "event": "feedback",
            "feedback_id": "fb_missing",
            "category": "missing_content",
            "source_id": "src_1",
        },
    ]
    feedback.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    evidence = ActionEvidence(feedback_ids=["fb_missing"], source_ids=["src_1"])
    proposed = ProposedAction(
        action_id="act_2",
        action_type="corpus_pr",
        classification="corpus_candidate",
        idempotency_key=deterministic_idempotency_key("corpus_pr", evidence),
        evidence=evidence,
        target_repo="grubbyhacker/ykmcorpus",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "existing_branches": [deterministic_branch_name("run-state-preflight", proposed)],
                "allowed_operations": ["issue.create", "pull.create"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-preflight",
                "mode": "state_only",
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
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_url="http://broker:8080",
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "fail"
    assert report.feedback_decisions_appended == 0
    assert report.checkpoint_advanced is False
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert not (intake / "feedback" / "curator-decisions.jsonl").exists()
    assert not (intake / "feedback" / "curator-state.json").exists()
    assert report.feedback_plan_paths


def test_state_only_feedback_decision_append_failure_is_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_positive","category":"positive_content"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-append-fail",
                "mode": "state_only",
                "enabled_actions": ["plan_feedback"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_append(*args: object, **kwargs: object) -> int:
        raise RuntimeError("synthetic append guard")

    monkeypatch.setattr("curator.runner.append_feedback_decisions", fail_append)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )

    assert report.status == "fail"
    assert report.feedback_decisions_appended == 0
    assert report.checkpoint_advanced is False
    assert any(
        failure["name"] == "feedback-decision-append"
        and "synthetic append guard" in failure["message"]
        for failure in report.partial_failures
    )
    assert not (intake / "feedback" / "curator-state.json").exists()


def test_state_only_curator_state_write_failure_is_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_positive","category":"positive_content"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-write-fail",
                "mode": "state_only",
                "enabled_actions": ["plan_feedback"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_state_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic state guard")

    monkeypatch.setattr("curator.runner.write_curator_state", fail_state_write)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="ignored", intake=intake, output=tmp_path / "output", task=task)
    )

    assert report.status == "fail"
    assert report.feedback_decisions_appended == 0
    assert report.checkpoint_advanced is False
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert (tmp_path / "output" / "run-report.json").exists()
    assert not (intake / "feedback" / "curator-decisions.jsonl").exists()


def test_state_only_broker_read_failure_blocks_state_commits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        json.dumps(
            {
                "feedback_id": "fb_positive",
                "category": "positive_content",
                "source_id": "src_1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-broker-read-fail",
                "mode": "state_only",
                "enabled_actions": ["reconcile", "plan_feedback"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://broker:8080/healthz"
        return httpx.Response(200)

    monkeypatch.setattr("curator.adapters.httpx.get", fake_get)
    monkeypatch.delenv("BROKER_AGENT_ID", raising=False)
    monkeypatch.delenv("BROKER_AGENT_SECRET", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_url="http://broker:8080",
            enable_broker_reads=True,
        )
    )

    assert report.status == "fail"
    assert report.feedback_decisions_appended == 0
    assert report.checkpoint_advanced is False
    assert not (intake / "feedback" / "curator-decisions.jsonl").exists()
    assert not (intake / "feedback" / "curator-state.json").exists()
    assert any(failure["name"] == "broker-pr-read" for failure in report.partial_failures)
    assert any(failure["name"] == "broker" for failure in report.partial_failures)


def test_state_only_appends_reconciliation_feedback_decisions_without_queue_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"missing_content","source_id":"src_1"}\n',
        encoding="utf-8",
    )
    claimed = intake / "uploads" / "claimed" / "upl_1"
    claimed.mkdir(parents=True)
    metadata = {
        "schema_version": "1",
        "upload_id": "upl_1",
        "state": "pr_opened",
        "run_id": "run-old",
        "pr_number": 44,
    }
    metadata_path = claimed / "curator.json"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-reconcile",
                "mode": "state_only",
                "enabled_actions": ["reconcile", "plan_feedback"],
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
                "pr_snapshots": [
                    {
                        "number": 44,
                        "state": "merged",
                        "body": "\n".join(
                            [
                                "YKM-Curator-Run: run-state-reconcile",
                                "YKM-Curator-Upload: upl_1",
                                "YKM-Curator-Feedback: fb_1",
                            ]
                        ),
                        "branch": "curator/run-state-reconcile/upload-upl-1",
                    }
                ],
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
    assert report.status == "pass"
    assert report.feedback_decisions_appended == 1
    assert decisions[0]["feedback_id"] == "fb_1"
    assert decisions[0]["plan_action_id"] == "reconciliation"
    assert decisions[0]["decision"] == "pr_opened"
    assert decisions[0]["pr_number"] == 44
    updated_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert report.upload_metadata_update_count == 1
    assert report.upload_metadata_update_paths == [str(metadata_path)]
    assert updated_metadata["upload_id"] == "upl_1"
    assert updated_metadata["state"] == "processed"
    assert updated_metadata["decision"] == "integrated"
    assert updated_metadata["run_id"] == "run-state-reconcile"
    assert updated_metadata["pr_number"] == 44
    assert claimed.exists()


def test_state_only_applies_closed_unmerged_upload_pr_as_deferred(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    claimed = intake / "uploads" / "claimed" / "upl_1"
    claimed.mkdir(parents=True)
    metadata = {
        "schema_version": "1",
        "upload_id": "upl_1",
        "state": "pr_opened",
        "run_id": "run-old",
        "pr_number": 44,
    }
    metadata_path = claimed / "curator.json"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-reconcile",
                "mode": "state_only",
                "enabled_actions": ["reconcile"],
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
                "pr_snapshots": [
                    {
                        "number": 44,
                        "state": "closed",
                        "body": "\n".join(
                            [
                                "YKM-Curator-Run: run-state-reconcile",
                                "YKM-Curator-Upload: upl_1",
                            ]
                        ),
                        "branch": "curator/run-state-reconcile/upload-upl-1",
                    }
                ],
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

    updated_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert report.status == "pass"
    assert report.upload_metadata_update_count == 1
    assert updated_metadata["upload_id"] == "upl_1"
    assert updated_metadata["state"] == "deferred"
    assert updated_metadata["decision"] == "deferred"
    assert updated_metadata["run_id"] == "run-state-reconcile"
    assert updated_metadata["pr_number"] == 44
    assert (
        updated_metadata["blocking_reason"]
        == "Closed-unmerged Curator PR can defer linked upload without a reentry trigger."
    )
    assert claimed.exists()


def test_state_only_records_closed_unmerged_pr_for_pending_upload_without_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    pending = intake / "uploads" / "pending" / "upl_duplicate"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_duplicate"}) + "\n",
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-reconcile",
                "mode": "state_only",
                "enabled_actions": ["reconcile"],
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
                "pr_snapshots": [
                    {
                        "number": 19,
                        "state": "closed",
                        "body": "\n".join(
                            [
                                "YKM-Curator-Run: run-state-reconcile",
                                "YKM-Curator-Upload: upl_duplicate",
                            ]
                        ),
                        "branch": "curator/run-state-reconcile/upload-upl-duplicate",
                    }
                ],
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

    metadata = json.loads((pending / "curator.json").read_text(encoding="utf-8"))
    assert report.status == "pass"
    assert report.upload_metadata_update_count == 1
    assert report.reconciliation["upload_transition_previews"][0]["from_state"] == "pending"
    assert metadata["upload_id"] == "upl_duplicate"
    assert metadata["state"] == "deferred"
    assert metadata["decision"] == "deferred"
    assert metadata["pr_number"] == 19
    assert metadata["branch"] == "curator/run-state-reconcile/upload-upl-duplicate"
    assert metadata["reentry_trigger"] is None
    assert pending.exists()


def test_state_only_does_not_apply_reconciliation_when_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"missing_content","source_id":"src_1"}\n',
        encoding="utf-8",
    )
    claimed = intake / "uploads" / "claimed" / "upl_1"
    claimed.mkdir(parents=True)
    metadata = {
        "schema_version": "1",
        "upload_id": "upl_1",
        "state": "pr_opened",
        "run_id": "run-old",
        "pr_number": 44,
    }
    metadata_path = claimed / "curator.json"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-no-reconcile",
                "mode": "state_only",
                "enabled_actions": ["plan_feedback"],
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
                "pr_snapshots": [
                    {
                        "number": 44,
                        "state": "merged",
                        "body": "\n".join(
                            [
                                "YKM-Curator-Run: run-state-no-reconcile",
                                "YKM-Curator-Upload: upl_1",
                                "YKM-Curator-Feedback: fb_1",
                            ]
                        ),
                        "branch": "curator/run-state-no-reconcile/upload-upl-1",
                    }
                ],
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

    assert report.status == "fail"
    assert report.checkpoint_advanced is False
    assert any(failure["name"] == "state-only" for failure in report.partial_failures)
    assert report.reconciliation["pr_reconciliation_count"] == 0
    assert report.reconciliation["feedback_decision_preview_count"] == 0
    assert report.upload_metadata_update_count == 0
    assert not (intake / "feedback" / "curator-decisions.jsonl").exists()
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == metadata
    assert not any(probe.name == "broker-pr-snapshots" for probe in report.probes)


def test_state_only_upload_metadata_update_failure_is_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    claimed = intake / "uploads" / "claimed" / "upl_1"
    claimed.mkdir(parents=True)
    metadata = {
        "schema_version": "1",
        "upload_id": "upl_1",
        "state": "pr_opened",
        "run_id": "run-old",
        "pr_number": 44,
    }
    metadata_path = claimed / "curator.json"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-state-update-fail",
                "mode": "state_only",
                "enabled_actions": ["reconcile"],
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
                "pr_snapshots": [
                    {
                        "number": 44,
                        "state": "merged",
                        "body": "YKM-Curator-Run: run-state-update-fail\nYKM-Curator-Upload: upl_1\n",
                        "branch": "curator/run-state-update-fail/upload-upl-1",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_transition(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic write guard")

    monkeypatch.setattr("curator.runner.transition_upload_metadata", fail_transition)
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

    assert report.status == "fail"
    assert report.upload_metadata_update_count == 0
    assert report.checkpoint_advanced is False
    assert any(
        failure["name"] == "upload-metadata-update"
        and "synthetic write guard" in failure["message"]
        for failure in report.partial_failures
    )
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == metadata
    assert not (intake / "feedback" / "curator-state.json").exists()


