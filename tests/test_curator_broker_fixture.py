from __future__ import annotations

import json
from pathlib import Path


from curator.models import (
    ActionEvidence,
    ProposedAction,
)
from curator.planning import deterministic_branch_name
from curator.state import deterministic_idempotency_key
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_broker_fixture_preflight_reports_existing_branch_collision(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"missing_content","source_id":"src_1"}\n',
        encoding="utf-8",
    )
    evidence = ActionEvidence(feedback_ids=["fb_1"], source_ids=["src_1"])
    proposed = ProposedAction(
        action_id="act_1",
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
                "existing_branches": [deterministic_branch_name("run-broker-branch", proposed)],
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
                "run_id": "run-broker-branch",
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
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "fail"
    assert any(
        failure["name"] == "broker-preflight" and "branch already exists" in failure["message"]
        for failure in report.partial_failures
    )


def test_broker_fixture_preflight_reports_existing_idempotency_key(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"needs_owner_action"}\n',
        encoding="utf-8",
    )
    existing_key = deterministic_idempotency_key("corpus_issue", ActionEvidence(feedback_ids=["fb_1"]))
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "existing_idempotency_keys": [existing_key],
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
                "run_id": "run-broker-idempotency",
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
    assert report.execution_intent_count == 1
    assert any(
        failure["name"] == "broker-preflight"
        and "idempotency key already exists" in failure["message"]
        for failure in report.partial_failures
    )


def test_broker_fixture_preflight_accepts_upload_review_previews(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_fixture_upload"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_fixture_upload"}) + "\n",
        encoding="utf-8",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
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
                "run_id": "run-upload-fixture",
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
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "fail"
    assert {failure["name"] for failure in report.partial_failures} == {"manual-live"}
    upload_preflight = next(
        probe for probe in report.probes if probe.name == "broker-upload-preflight"
    )
    assert upload_preflight.status == "pass"
    assert upload_preflight.details == {"preview_count": 1}
    assert pending.exists()


def test_broker_fixture_preflight_reports_upload_review_collisions(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_fixture_collision"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_fixture_collision"}) + "\n",
        encoding="utf-8",
    )
    idempotency_key = deterministic_idempotency_key(
        "upload", ActionEvidence(upload_ids=["upl_fixture_collision"])
    )
    branch = (
        "curator/run-upload-fixture-collision/upload-upl-fixture-collision-"
        f"{idempotency_key.rsplit(':', maxsplit=1)[-1][:12]}"
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "existing_branches": [branch],
                "existing_idempotency_keys": [idempotency_key],
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
                "run_id": "run-upload-fixture-collision",
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
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "fail"
    failures = [
        failure for failure in report.partial_failures if failure["name"] == "broker-upload-preflight"
    ]
    assert [failure["message"] for failure in failures] == [
        "upload review idempotency key already exists in broker fixture",
        "upload review branch already exists in broker fixture",
    ]
    assert report.github_mutation_count == 0
    assert pending.exists()


def test_fixture_execution_simulation_requires_broker_fixture(
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
                "run_id": "run-sim-no-fixture",
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
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            simulate_execution=True,
        )
    )

    assert report.status == "fail"
    assert report.simulated_execution_count == 0
    assert any(failure["name"] == "fixture-execution" for failure in report.partial_failures)


def test_fixture_execution_simulation_records_results_without_real_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"needs_owner_action"}\n',
        encoding="utf-8",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
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
                "run_id": "run-sim",
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
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_fixture=broker_fixture,
            simulate_execution=True,
        )
    )

    assert report.status == "fail"
    assert report.github_mutation_count == 0
    assert report.simulated_execution_count == 1
    assert report.simulated_execution_results[0]["operation"] == "issue.create"
    assert report.simulated_execution_results[0]["status"] == "simulated"
    assert not (intake / "feedback" / "curator-state.json").exists()


