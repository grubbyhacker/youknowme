from __future__ import annotations

import json
import os
import time
from pathlib import Path


from curator.models import (
    FeedbackPlan,
)
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_curator_dry_run_writes_report(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_20260608_test"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text('{"upload_id":"upl_20260608_test"}\n', encoding="utf-8")
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir()
    feedback.write_text('{"event":"feedback","feedback_id":"fb_1"}\n', encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "query-log.jsonl").write_text('{"event":"query"}\n', encoding="utf-8")
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text('{"run_id":"run-1","purpose":"dry run"}\n', encoding="utf-8")

    for name in ("GITHUB_TOKEN", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-1",
            intake=intake,
            logs=logs,
            output=output,
            task=task,
        )
    )

    assert report.status == "pass"
    assert report.upload_queue_counts["pending"] == 1
    assert report.pending_uploads == ["upl_20260608_test"]
    assert report.feedback_count == 1
    assert report.query_log_count == 1
    assert (output / "run-report.json").exists()
    assert (output / "run-report.md").exists()
    persisted = json.loads((output / "run-report.json").read_text(encoding="utf-8"))
    assert persisted["run_id"] == "run-1"
    assert persisted["enabled_actions"] == ["plan_feedback", "plan_uploads", "reconcile"]
    assert persisted["feedback_checkpoint"] == {
        "path": "feedback/feedback.jsonl",
        "previous_byte_offset": 0,
        "next_byte_offset": len('{"event":"feedback","feedback_id":"fb_1"}\n'),
    }
    assert len(persisted["feedback_plan_paths"]) == 2


def test_curator_task_model_budget_field_uses_strict_contract_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    output.mkdir()
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps({"model_call_budget": {"max_calls_per_run": -1}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "fail"
    task_probe = next(probe for probe in report.probes if probe.name == "task")
    assert task_probe.status == "fail"
    assert "task contract invalid" in task_probe.message
    assert report.run_id == "fallback-run"
    assert report.included_feedback_ids == []


def test_curator_reads_task_embedded_in_broker_task_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text('{"event":"feedback","feedback_id":"fb_1"}\n', encoding="utf-8")
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "broker-run",
                "task": json.dumps(
                    {
                        "schema_version": "1",
                        "run_id": "broker-run",
                        "mode": "dry_run",
                        "enabled_actions": ["plan_feedback"],
                        "feedback_soft_action_threshold": 3,
                    }
                ),
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
                "deliverables": ["/output/run-report.json", "/output/run-report.md"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "pass"
    assert report.run_id == "broker-run"
    assert report.enabled_actions == ["plan_feedback"]
    assert report.included_feedback_ids == ["fb_1"]
    assert report.included_upload_ids == []
    assert report.task is not None
    assert report.task["feedback_soft_action_threshold"] == 3
    task_probe = next(probe for probe in report.probes if probe.name == "task")
    assert task_probe.status == "pass"
    assert "broker task contract loaded" in task_probe.message


def test_curator_applies_broker_upload_id_parameters_to_embedded_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    for upload_id in ("upl_first", "upl_second"):
        pending = intake / "uploads" / "pending" / upload_id
        pending.mkdir(parents=True)
        (pending / "manifest.json").write_text(
            json.dumps({"upload_id": upload_id}) + "\n",
            encoding="utf-8",
        )
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "broker-run",
                "task": json.dumps(
                    {
                        "schema_version": "1",
                        "run_id": "${SANDBOX_RUN_ID}",
                        "mode": "dry_run",
                        "enabled_actions": ["plan_uploads"],
                        "github_mutation_budget": {
                            "max_new_objects_per_run": 0,
                            "upload": 0,
                            "feedback": 0,
                        },
                    }
                ),
                "parameters": {"upload_ids": ["upl_second"]},
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "pass"
    assert report.run_id == "broker-run"
    assert report.mode == "dry_run"
    assert report.github_mutation_budget["max_new_objects_per_run"] == 0
    assert report.included_upload_ids == ["upl_second"]
    assert report.task is not None
    assert report.task["upload_ids"] == ["upl_second"]
    assert [preview["upload_id"] for preview in report.upload_review_previews] == ["upl_second"]


def test_curator_rejects_unsupported_broker_task_parameters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "broker-run",
                "task": json.dumps(
                    {
                        "schema_version": "1",
                        "run_id": "${SANDBOX_RUN_ID}",
                        "mode": "dry_run",
                        "enabled_actions": ["plan_uploads"],
                    }
                ),
                "parameters": {"mode": "manual_live"},
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "fail"
    assert report.run_id == "fallback-run"
    task_probe = next(probe for probe in report.probes if probe.name == "task")
    assert task_probe.status == "fail"
    assert "unsupported keys: ['mode']" in task_probe.message
    assert report.github_mutation_budget == {}
    assert report.included_upload_ids == []


def test_curator_rejects_broker_task_contract_with_non_json_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "broker-run",
                "task": "run the Curator",
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "fail"
    assert report.run_id == "fallback-run"
    assert (output / "run-report.json").exists()
    task_probe = next(probe for probe in report.probes if probe.name == "task")
    assert task_probe.status == "fail"
    assert "broker task string must contain Curator task JSON" in task_probe.message


def test_curator_replaces_broker_task_run_id_placeholder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "broker-run",
                "task": json.dumps(
                    {
                        "schema_version": "1",
                        "run_id": "${SANDBOX_RUN_ID}",
                        "mode": "dry_run",
                        "enabled_actions": ["reconcile"],
                    }
                ),
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "pass"
    assert report.run_id == "broker-run"
    assert report.task is not None
    assert report.task["run_id"] == "broker-run"


def test_curator_rejects_broker_task_run_id_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    output = tmp_path / "output"
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "broker-run",
                "task": json.dumps({"schema_version": "1", "run_id": "curator-run"}),
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="fallback-run",
            intake=intake,
            output=output,
            task=task,
        )
    )

    assert report.status == "fail"
    task_probe = next(probe for probe in report.probes if probe.name == "task")
    assert task_probe.status == "fail"
    assert "does not match broker run_id" in task_probe.message


def test_curator_dry_run_fails_when_forbidden_secret_env_is_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    output.mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-value")

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-secret", intake=intake, output=output)
    )

    assert report.status == "fail"
    forbidden = next(probe for probe in report.probes if probe.name == "forbidden-env")
    assert forbidden.status == "fail"
    assert "OPENROUTER_API_KEY" in forbidden.details["names"]
    assert "secret-value" not in json.dumps(forbidden.model_dump(mode="json"))
    assert report.included_feedback_ids == []
    assert not (output / "feedback").exists()
    assert not (intake / "feedback" / "curator-state.json").exists()


def test_feedback_plan_write_failure_is_reported_without_crashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import curator.runner as runner

    intake = tmp_path / "intake"
    output = tmp_path / "output"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text('{"event":"feedback","feedback_id":"fb_1"}\n', encoding="utf-8")
    original_write = runner._write_feedback_plan

    def fail_intake_feedback_plan(runs_dir: Path, plan: FeedbackPlan) -> Path:
        if str(runs_dir).startswith(str(intake)):
            raise RuntimeError("synthetic feedback plan guard")
        return original_write(runs_dir, plan)

    monkeypatch.setattr("curator.runner._write_feedback_plan", fail_intake_feedback_plan)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-plan-fail", intake=intake, output=output)
    )

    assert report.status == "fail"
    assert len(report.feedback_plan_paths) == 1
    assert report.feedback_plan_paths[0].startswith(str(output))
    assert any(
        failure["name"] == "feedback-plan-write"
        and "synthetic feedback plan guard" in failure["message"]
        for failure in report.partial_failures
    )
    assert (output / "run-report.json").exists()


def test_curator_dry_run_can_require_broker_probe(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    output.mkdir()
    monkeypatch.delenv("BROKER_AGENT_ID", raising=False)
    monkeypatch.delenv("BROKER_AGENT_SECRET", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-broker",
            intake=intake,
            output=output,
            broker_url="http://broker:8080",
            required_broker=True,
        )
    )

    assert report.status == "fail"
    broker = next(probe for probe in report.probes if probe.name == "broker")
    assert broker.status == "fail"
    assert broker.details["missing"] == ["BROKER_AGENT_ID", "BROKER_AGENT_SECRET"]


def test_curator_live_lock_exits_before_planning(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    output.mkdir()
    lock = tmp_path / "curator.lock"
    lock.write_text('{"run_id":"other"}\n', encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-lock", intake=intake, output=output, lock_path=lock)
    )

    assert report.status == "fail"
    assert report.feedback_count == 0
    assert report.upload_queue_counts["pending"] == 0
    assert next(probe for probe in report.probes if probe.name == "lock").status == "fail"


def test_curator_stale_lock_requires_recovery(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    output.mkdir()
    lock = tmp_path / "curator.lock"
    lock.write_text('{"run_id":"old"}\n', encoding="utf-8")
    old = time.time() - 8000
    os.utime(lock, (old, old))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    blocked = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-stale", intake=intake, output=output, lock_path=lock)
    )
    recovered = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-stale",
            intake=intake,
            output=output,
            lock_path=lock,
            recover_stale_lock=True,
        )
    )

    assert blocked.status == "fail"
    assert "requires explicit recovery" in next(
        probe for probe in blocked.probes if probe.name == "lock"
    ).message
    assert recovered.status == "pass"
    assert not lock.exists()


