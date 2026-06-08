from __future__ import annotations

import json
from pathlib import Path

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
    assert forbidden.details == {"names": ["OPENROUTER_API_KEY"]}


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
