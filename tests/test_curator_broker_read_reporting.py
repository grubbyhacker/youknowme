from __future__ import annotations

import json
from pathlib import Path

import httpx

from ykm.curator import CuratorDryRunConfig, run_curator_dry_run





def test_runner_records_http_broker_readonly_preflight_descriptors(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_1","category":"missing_content","source_id":"src_1"}\n',
        encoding="utf-8",
    )
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-http-broker",
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

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://broker:8080/healthz"
        assert kwargs["timeout"] == 5
        return httpx.Response(200)

    monkeypatch.setattr("curator.adapters.httpx.get", fake_get)
    monkeypatch.setenv("BROKER_AGENT_ID", "agent")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="ignored",
            intake=intake,
            output=tmp_path / "output",
            task=task,
            broker_url="http://broker:8080",
        )
    )

    assert report.status == "fail"
    assert {failure["name"] for failure in report.partial_failures} == {
        "agentic-feedback",
        "manual-live-feedback",
    }
    assert next(probe for probe in report.probes if probe.name == "broker").status == "pass"
    preflight = next(probe for probe in report.probes if probe.name == "broker-preflight")
    assert preflight.status == "skip"
    assert [request["operation"] for request in preflight.details["requests"]] == [
        "pull.list",
        "issue.search",
    ]
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## Broker Read Preflight" in markdown
    assert "`pull.list` `/repos/grubbyhacker/ykmcorpus/pulls`" in markdown


def test_runner_records_upload_review_broker_read_descriptors(
    tmp_path: Path, monkeypatch
) -> None:
    intake = tmp_path / "intake"
    pending = intake / "uploads" / "pending" / "upl_upload_read"
    pending.mkdir(parents=True)
    (pending / "manifest.json").write_text(
        json.dumps({"upload_id": "upl_upload_read"}) + "\n",
        encoding="utf-8",
    )

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://broker:8080/healthz"
        assert kwargs["timeout"] == 5
        return httpx.Response(200)

    monkeypatch.setattr("curator.adapters.httpx.get", fake_get)
    monkeypatch.setenv("BROKER_AGENT_ID", "agent")
    monkeypatch.setenv("BROKER_AGENT_SECRET", "broker-secret")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-upload-read",
            intake=intake,
            output=tmp_path / "output",
            broker_url="http://broker:8080",
        )
    )

    assert report.status == "pass"
    read_probe = next(
        probe for probe in report.probes if probe.name == "broker-upload-read-preflight"
    )
    assert read_probe.status == "skip"
    requests = read_probe.details["requests"]
    assert [request["operation"] for request in requests] == ["pull.list", "issue.search"]
    assert requests[0]["params"]["head"].startswith(
        "grubbyhacker:curator/run-upload-read/upload-upl-upload-read-"
    )
    assert requests[1]["params"]["q"].startswith("upload:")
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "`pull.list` `/repos/grubbyhacker/ykmcorpus/pulls`" in markdown
    assert pending.exists()


def test_runner_records_broker_pr_reconciliation_read_descriptors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "pr_snapshots": [
                    {
                        "number": 44,
                        "state": "open",
                        "body": "YKM-Curator-Run: run-pr-read",
                        "branch": "curator/run-pr-read/upload-upl-1",
                    }
                ],
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
                "run_id": "run-pr-read",
                "mode": "dry_run",
                "enabled_actions": ["reconcile"],
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

    assert report.status == "pass"
    read_probe = next(probe for probe in report.probes if probe.name == "broker-pr-read-preflight")
    assert read_probe.status == "skip"
    assert [request["operation"] for request in read_probe.details["requests"][:3]] == [
        "pull.list",
        "issue.search",
        "pull.read",
    ]
    assert read_probe.details["requests"][2]["path"] == "/repos/grubbyhacker/ykmcorpus/pulls/44"
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "`pull.read` `/repos/grubbyhacker/ykmcorpus/pulls/44`" in markdown


def test_runner_records_broker_issue_reconciliation_read_descriptors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text("", encoding="utf-8")
    decisions = intake / "feedback" / "curator-decisions.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "feedback_id": "fb_blocked",
                "run_id": "old-run",
                "plan_action_id": "act_old",
                "decision": "deferred",
                "issue_number": 77,
                "reentry_trigger": "owner_input_resolved",
                "reason": "waiting on issue",
                "timestamp": "2026-06-08T12:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    deferred = intake / "uploads" / "deferred" / "upl_blocked"
    deferred.mkdir(parents=True)
    (deferred / "manifest.json").write_text('{"upload_id":"upl_blocked"}\n', encoding="utf-8")
    (deferred / "curator.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "upload_id": "upl_blocked",
                "state": "deferred",
                "run_id": "old-run",
                "blocking_issue_number": 78,
                "reentry_trigger": "owner_input_resolved",
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
                "run_id": "run-issue-read",
                "mode": "dry_run",
                "enabled_actions": ["reconcile"],
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
        )
    )

    assert report.status == "pass"
    read_probe = next(probe for probe in report.probes if probe.name == "broker-issue-read-preflight")
    assert [request["operation"] for request in read_probe.details["requests"]] == [
        "issue.read",
        "issue.comments",
        "issue.read",
        "issue.comments",
    ]
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "`issue.read` `/repos/grubbyhacker/ykmcorpus/issues/77`" in markdown
    assert "`issue.read` `/repos/grubbyhacker/ykmcorpus/issues/78`" in markdown


