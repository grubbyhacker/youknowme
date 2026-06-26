from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


from curator.models import (
    CuratorProbe,
    CuratorRunReport,
)
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run
from curator.runner import (
    write_curator_reports,
)





def test_runner_uses_broker_fixture_pr_snapshots_for_offline_reconciliation(
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
    (claimed / "manifest.json").write_text('{"upload_id":"upl_1"}\n', encoding="utf-8")
    metadata = {
        "schema_version": "1",
        "upload_id": "upl_1",
        "state": "pr_opened",
        "run_id": "run-old",
        "branch": "curator/run-pr-fixture/upload-upl-1",
        "pr_number": 44,
    }
    curator_metadata = claimed / "curator.json"
    curator_metadata.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
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
                                "YKM-Curator-Run: run-pr-fixture",
                                "YKM-Curator-Upload: upl_1",
                                "YKM-Curator-Feedback: fb_1",
                            ]
                        ),
                        "branch": "curator/run-pr-fixture/upload-upl-1",
                        "checks_conclusion": "success",
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
            run_id="run-pr-fixture",
            intake=intake,
            output=tmp_path / "output",
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "pass"
    assert report.reconciliation["pr_reconciliation_count"] == 1
    assert report.reconciliation["pr_reconciliations"][0]["pr_state"] == "merged"
    assert report.reconciliation["upload_transition_preview_count"] == 1
    preview = report.reconciliation["upload_transition_previews"][0]
    assert preview["upload_id"] == "upl_1"
    assert preview["from_state"] == "pr_opened"
    assert preview["to_state"] == "processed"
    assert preview["validation"] == "accepted"
    assert report.reconciliation["feedback_decision_preview_count"] == 1
    decision_preview = report.reconciliation["feedback_decision_previews"][0]
    assert decision_preview["feedback_id"] == "fb_1"
    assert decision_preview["to_decision"] == "pr_opened"
    assert decision_preview["validation"] == "accepted"
    assert json.loads(curator_metadata.read_text(encoding="utf-8")) == metadata
    assert next(probe for probe in report.probes if probe.name == "broker-pr-snapshots").status == (
        "pass"
    )
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## PR Reconciliation" in markdown
    assert "PR `#44`: `merged`" in markdown
    assert "## Upload Transition Previews" in markdown
    assert "`upl_1` from PR `#44`: `pr_opened` -> `processed`" in markdown
    assert "## Feedback Decision Previews" in markdown
    assert "`fb_1` from PR `#44`: `none` -> `pr_opened`" in markdown


def test_runner_reports_invalid_broker_fixture_pr_snapshots(
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
                "pr_snapshots": [{"number": "not-an-int", "state": "merged"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    report = run_curator_dry_run(
        CuratorDryRunConfig(
            run_id="run-bad-pr-fixture",
            intake=intake,
            output=tmp_path / "output",
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "fail"
    snapshot_failure = next(
        failure for failure in report.partial_failures if failure["name"] == "broker-pr-snapshots"
    )
    assert "PR snapshots unreadable" in snapshot_failure["message"]
    assert report.reconciliation["pr_reconciliation_count"] == 0


def test_runner_uses_broker_fixture_issue_snapshots_for_reentry_previews(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    feedback = intake / "feedback" / "feedback.jsonl"
    feedback.parent.mkdir(parents=True)
    feedback.write_text(
        '{"event":"feedback","feedback_id":"fb_blocked","category":"needs_owner_action"}\n',
        encoding="utf-8",
    )
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
    metadata = {
        "schema_version": "1",
        "upload_id": "upl_blocked",
        "state": "deferred",
        "run_id": "old-run",
        "blocking_issue_number": 77,
        "reentry_trigger": "owner_input_resolved",
    }
    metadata_path = deferred / "curator.json"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "reachable": True,
                "issue_snapshots": [{"number": 77, "state": "closed"}],
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
                "run_id": "run-issue-fixture",
                "mode": "state_only",
                "enabled_actions": ["reconcile", "plan_feedback", "plan_uploads"],
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

    assert report.status == "pass"
    assert report.reconciliation["feedback_reentry_preview_count"] == 1
    assert report.reconciliation["feedback_reentry_previews"][0]["feedback_id"] == "fb_blocked"
    assert report.reconciliation["upload_transition_preview_count"] == 1
    assert report.reconciliation["upload_transition_previews"][0]["to_state"] == "claimed"
    appended_decisions = [
        json.loads(line) for line in decisions.read_text(encoding="utf-8").splitlines()
    ]
    assert report.feedback_decisions_appended == 1
    assert len(appended_decisions) == 2
    assert appended_decisions[1]["feedback_id"] == "fb_blocked"
    assert appended_decisions[1]["plan_action_id"] == "reconciliation"
    assert appended_decisions[1]["decision"] == "deferred"
    assert appended_decisions[1]["reentry_trigger"] == "next_run"
    assert appended_decisions[1]["issue_number"] == 77
    updated_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert report.upload_metadata_update_count == 1
    assert report.upload_metadata_update_paths == [str(metadata_path)]
    assert updated_metadata["upload_id"] == "upl_blocked"
    assert updated_metadata["state"] == "claimed"
    assert updated_metadata["run_id"] == "run-issue-fixture"
    assert updated_metadata["blocking_issue_number"] == 77
    assert deferred.exists()
    assert next(
        probe for probe in report.probes if probe.name == "broker-issue-snapshots"
    ).status == "pass"
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## Feedback Reentry Previews" in markdown
    assert "`fb_blocked` from issue `#77`" in markdown
    assert "`upl_blocked` from issue `#77`: `deferred` -> `claimed`" in markdown


def test_run_report_markdown_renders_pr_reconciliation(tmp_path: Path) -> None:
    timestamp = datetime.fromisoformat("2026-06-08T12:00:00+00:00")
    report = CuratorRunReport(
        run_id="run-report-pr",
        created_at=timestamp,
        started_at=timestamp,
        completed_at=timestamp,
        status="pass",
        intake_path=str(tmp_path / "intake"),
        output_path=str(tmp_path / "output"),
        lock_path=str(tmp_path / "curator.lock"),
        feedback_window={"start_offset": 0, "end_offset": 0},
        feedback_checkpoint={
            "path": "feedback/feedback.jsonl",
            "previous_byte_offset": 0,
            "next_byte_offset": 0,
        },
        checkpoint_advanced=False,
        included_feedback_ids=[],
        feedback_decision_count=0,
        upload_queue_counts={},
        pending_uploads=[],
        proposed_action_count=0,
        feedback_count=0,
        query_log_count=0,
        reconciliation={
            "pr_state_counts": {"closed_unmerged": 1},
            "pr_reconciliations": [
                {
                    "pr_number": 44,
                    "pr_state": "closed_unmerged",
                    "reason": "PR is closed without a merge marker.",
                }
            ],
            "upload_transition_previews": [
                {
                    "upload_id": "upl_1",
                    "pr_number": 44,
                    "branch": "curator/run-report-pr/upload-upl-1",
                    "from_state": "pr_opened",
                    "to_state": "deferred",
                    "validation": "accepted",
                    "reason": "Closed-unmerged Curator PR can defer linked upload.",
                }
            ],
            "review_guidance_candidates": [
                {
                    "candidate_id": "pr-44-review-thread-123",
                    "pr_number": 44,
                    "author_login": "grubbyhacker",
                    "body": "Never create backup files in git.",
                    "guidance": "Never create backup files in git.",
                    "path": ".ykm/corpus-policy.yaml.bak",
                    "line": 1,
                    "comment_id": "123",
                    "source": "review_thread",
                    "reason": "Owner inline review comment on a Curator PR can become guidance.",
                }
            ],
        },
        probes=[CuratorProbe(name="test", status="pass", message="ok")],
    )

    write_curator_reports(report, tmp_path / "output")

    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## PR Reconciliation" in markdown
    assert "State counts: `closed_unmerged`: `1`" in markdown
    assert "PR `#44`: `closed_unmerged`" in markdown
    assert "## Upload Transition Previews" in markdown
    assert "`upl_1` from PR `#44`: `pr_opened` -> `deferred`" in markdown
    assert "## Review Guidance Candidates" in markdown
    assert "`pr-44-review-thread-123` from PR `#44` on `.ykm/corpus-policy.yaml.bak`:1" in markdown


