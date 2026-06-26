from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_malformed_feedback_records_are_reported_without_dropping_valid_records(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    valid_line = '{"event":"feedback","feedback_id":"fb_1","category":"positive_content"}\n'
    feedback.write_text(
        valid_line
        + '{"event":"feedback","feedback_id":"fb_bad"\n'
        + '{"event":"feedback","category":"missing_content"}\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(run_id="run-bad-feedback", intake=intake, output=tmp_path / "output")
    )

    assert report.status == "fail"
    assert report.included_feedback_ids == ["fb_1"]
    assert report.proposed_action_count == 1
    assert report.input_error_count == 2
    assert report.validation_failure_count == 2
    assert [error["category"] for error in report.input_errors] == [
        "invalid_json",
        "invalid_schema",
    ]
    assert {failure["name"] for failure in report.partial_failures} >= {"feedback-input"}
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## Input Errors" in markdown
    plan = json.loads(
        (tmp_path / "output" / "feedback" / "runs" / "run-bad-feedback" / "feedback-plan.json")
        .read_text(encoding="utf-8")
    )
    assert plan["included_feedback_ids"] == ["fb_1"]


def test_ykm_curator_dry_run_entrypoint_remains_compatible(tmp_path: Path, monkeypatch) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("GITHUB_TOKEN", None)
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ykm.curator_cli",
            "--run-id",
            "cli-run",
            "--intake",
            str(intake),
            "--output",
            str(output),
            "--no-task",
            "--lock-path",
            str(tmp_path / "curator.lock"),
        ],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["run_id"] == "cli-run"
    assert payload["status"] == "pass"
    assert (output / "run-report.json").exists()


def test_native_curator_cli_run_entrypoint(tmp_path: Path) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    intake.mkdir()
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("GITHUB_TOKEN", None)
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "curator.cli",
            "run",
            "--run-id",
            "native-cli-run",
            "--intake",
            str(intake),
            "--output",
            str(output),
            "--no-task",
            "--lock-path",
            str(tmp_path / "curator.lock"),
        ],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["run_id"] == "native-cli-run"
    assert payload["status"] == "pass"


def test_native_curator_cli_inspect_report_summarizes_valid_report(tmp_path: Path) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text('{"event":"feedback","feedback_id":"fb_1"}\n', encoding="utf-8")
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("GITHUB_TOKEN", None)
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "curator.cli",
            "run",
            "--run-id",
            "native-cli-report",
            "--intake",
            str(intake),
            "--output",
            str(output),
            "--no-task",
            "--lock-path",
            str(tmp_path / "curator.lock"),
        ],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "curator.cli",
            "inspect-report",
            str(output / "run-report.json"),
        ],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary["run_id"] == "native-cli-report"
    assert summary["status"] == "pass"
    assert summary["enabled_actions"] == ["plan_feedback", "plan_uploads", "reconcile"]
    assert summary["checkpoint_advanced"] is False
    assert summary["feedback_checkpoint"] == {
        "path": "feedback/feedback.jsonl",
        "previous_byte_offset": 0,
        "next_byte_offset": len('{"event":"feedback","feedback_id":"fb_1"}\n'),
    }
    assert summary["feedback_count"] == 1
    assert summary["included_feedback_count"] == 1
    assert summary["referenced_upload_count"] == 0
    assert summary["referenced_source_count"] == 0
    assert summary["referenced_section_count"] == 0
    assert summary["referenced_result_count"] == 0
    assert summary["validation_failure_count"] == 0
    assert summary["partial_failure_count"] == 0
    assert summary["partial_failure_names"] == []
    assert summary["github_mutation_count"] == 0
    assert summary["pr_reconciliation_count"] == 0
    assert summary["pr_state_counts"] == {}
    assert summary["simulated_execution_count"] == 0
    assert summary["capacity_deferral_count"] == 0
    assert summary["capacity_deferred_feedback_count"] == 0
    assert summary["model_token_count"] == 0


def test_native_curator_cli_inspect_report_rejects_invalid_contract(tmp_path: Path) -> None:
    report = tmp_path / "run-report.json"
    report.write_text('{"schema_version":"1","run_id":"bad"}\n', encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [sys.executable, "-m", "curator.cli", "inspect-report", str(report)],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 1
    assert "invalid Curator report" in result.stderr


def test_native_curator_cli_inspect_report_summarizes_failures(tmp_path: Path) -> None:
    intake = tmp_path / "intake"
    output = tmp_path / "output"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text('{"event":"feedback","feedback_id":\n', encoding="utf-8")
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("GITHUB_TOKEN", None)
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "curator.cli",
            "run",
            "--run-id",
            "native-cli-failed-report",
            "--intake",
            str(intake),
            "--output",
            str(output),
            "--no-task",
            "--lock-path",
            str(tmp_path / "curator.lock"),
        ],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "curator.cli",
            "inspect-report",
            str(output / "run-report.json"),
        ],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary["status"] == "fail"
    assert summary["validation_failure_count"] == 1
    assert summary["input_error_count"] == 1
    assert summary["partial_failure_names"] == ["feedback", "feedback-input"]


def test_native_curator_cli_inspect_task_validates_without_intake(tmp_path: Path) -> None:
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "inspect-run",
                "mode": "state_only",
                "enabled_actions": ["reconcile", "plan_feedback"],
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
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [sys.executable, "-m", "curator.cli", "inspect-task", str(task)],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["run_id"] == "inspect-run"
    assert payload["mode"] == "state_only"


def test_native_curator_cli_inspect_task_accepts_broker_task_contract(tmp_path: Path) -> None:
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "run_id": "inspect-broker-run",
                "task": json.dumps(
                    {
                        "schema_version": "1",
                        "run_id": "inspect-broker-run",
                        "mode": "dry_run",
                        "enabled_actions": ["reconcile"],
                    }
                ),
                "repo": "grubbyhacker/ykmcorpus",
                "base_branch": "main",
                "branch": "curator/inspect-broker-run/task",
                "worker_agent_id": "ykm-curator",
                "broker_remote_url": "http://broker:8080/git/grubbyhacker/ykmcorpus.git",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [sys.executable, "-m", "curator.cli", "inspect-task", str(task)],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["run_id"] == "inspect-broker-run"
    assert payload["mode"] == "dry_run"
    assert payload["enabled_actions"] == ["reconcile"]


def test_native_curator_cli_inspect_task_rejects_invalid_contract(tmp_path: Path) -> None:
    task = tmp_path / "task.json"
    task.write_text('{"schema_version":"2","run_id":"bad"}\n', encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [sys.executable, "-m", "curator.cli", "inspect-task", str(task)],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 1
    assert "invalid Curator task" in result.stderr


