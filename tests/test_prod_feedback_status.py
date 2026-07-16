from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path("scripts/prod-feedback-status.sh")


def _remote_program() -> str:
    script = SCRIPT.read_text(encoding="utf-8")
    return script.split("<<'PY'\n", maxsplit=1)[1].rsplit("\nPY\n", maxsplit=1)[0]


def _run_remote_program(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict:
    monkeypatch.setattr(sys, "argv", ["prod-feedback-status", "-"])
    namespace = {"__name__": "__main__"}
    exec(compile(_remote_program(), str(SCRIPT), "exec"), namespace)
    return json.loads(capsys.readouterr().out)


def test_prod_feedback_status_reports_empty_runs_only_when_operator_api_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == (
            "sudo",
            "-n",
            "/usr/local/libexec/ykm-curator-run-reports",
            "--limit",
            "5",
        )
        assert kwargs == {
            "capture_output": True,
            "check": False,
            "text": True,
            "timeout": 10,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps({"runs": []}), "")

    monkeypatch.setattr("subprocess.run", fake_run)

    payload = _run_remote_program(monkeypatch, capsys)

    assert payload["recent_curator_runs"] == {
        "availability": "available",
        "source": "sandbox_broker_operator_api",
        "runs": [],
    }


def test_prod_feedback_status_reports_missing_managed_reader_as_inaccessible(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr("subprocess.run", fake_run)

    payload = _run_remote_program(monkeypatch, capsys)

    assert payload["recent_curator_runs"] == {
        "availability": "inaccessible",
        "source": "sandbox_broker_operator_api",
        "runs": [],
        "reason": "managed_api_reader_unavailable",
        "required_capability": "read-only broker run-report access",
    }


def test_prod_feedback_status_filters_managed_run_report_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(command: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "cur_123",
                            "profile": "ykm-curator-live",
                            "status": "failed",
                            "feedback_decisions_appended": 0,
                            "private_broker_metadata": "must not be printed",
                        },
                        {"run_id": "other_456", "profile": "unrelated-profile"},
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    payload = _run_remote_program(monkeypatch, capsys)

    assert payload["recent_curator_runs"] == {
        "availability": "available",
        "source": "sandbox_broker_operator_api",
        "runs": [
            {
                "run_id": "cur_123",
                "profile": "ykm-curator-live",
                "status": "failed",
                "feedback_decisions_appended": 0,
            }
        ],
    }
