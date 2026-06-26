from __future__ import annotations

import json
from pathlib import Path


from curator.models import (
    CuratorPrReconciliation,
    CuratorPrReviewSnapshot,
    CuratorPrReviewThreadSnapshot,
    CuratorPrSnapshot,
    ExecutionResult,
    PrRepairResult,
)
from curator.pr_repair import (
    _review_request_comment,
)
from ykm.curator import CuratorDryRunConfig, run_curator_dry_run
from curator.runner import (
    _complete_pr_repair_handoffs,
    _write_pending_pr_repair_handoffs,
)





def test_runner_treats_pr_repair_workflow_guardrail_skip_as_handled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    (intake / "feedback").mkdir(parents=True)
    (intake / "feedback" / "feedback.jsonl").write_text("", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-pr-repair",
                "mode": "dry_run",
                "enabled_actions": ["reconcile", "repair_prs"],
                "pr_repair_executor": "codex_proxy",
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
                        "number": 5,
                        "state": "open",
                        "body": "YKM-Curator-Run: run-pr-repair\n",
                        "branch": "curator/run-pr-repair/upload-upl-20260606",
                        "labels": ["ym-curator: needs work"],
                        "review_decision": "changes_requested",
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

    def fake_pr_repairs(**kwargs):
        return [
            PrRepairResult(
                pr_number=5,
                branch="curator/run-pr-repair/upload-upl-20260606",
                pr_state="changes_requested",
                executor="codex_proxy",
                model=kwargs["model"],
                status="skipped",
                message=(
                    "Codex PR repair only changed GitHub workflow files. The Curator discarded "
                    "those edits because the GitHub App cannot push workflow changes."
                ),
                changed_files=[],
                validation_command=kwargs["validation_command"],
            )
        ]

    monkeypatch.setattr("curator.runner.execute_pr_repairs", fake_pr_repairs)

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

    assert report.status == "pass", report.partial_failures
    assert report.partial_failures == []
    assert report.pr_repair_results[0]["status"] == "skipped"
    assert any(probe.name == "pr-repair" and probe.status == "pass" for probe in report.probes)


def test_runner_fixture_repairs_actionable_curator_pr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    (intake / "feedback").mkdir(parents=True)
    (intake / "feedback" / "feedback.jsonl").write_text("", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-pr-repair",
                "mode": "dry_run",
                "enabled_actions": ["reconcile", "repair_prs"],
                "pr_repair_executor": "fixture",
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
                        "number": 5,
                        "state": "open",
                        "body": (
                            "YKM-Curator-Run: run-pr-repair\n"
                            "YKM-Curator-Upload: upl_20260606_051912_4edc604c\n"
                        ),
                        "branch": "curator/run-pr-repair/upload-upl-20260606",
                        "labels": ["ym-curator: needs work"],
                        "review_decision": "changes_requested",
                        "checks_conclusion": "missing",
                        "review_comments": [
                            "Validation is not actually running for this PR.",
                        ],
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

    assert report.status == "pass"
    assert report.enabled_actions == ["reconcile", "repair_prs"]
    assert report.pr_repair_result_count == 1
    assert report.pr_repair_validation_failure_count == 0
    assert report.pr_repair_results[0]["pr_number"] == 5
    assert report.pr_repair_results[0]["status"] == "validated"
    assert "ready for review again" in report.pr_repair_results[0]["review_request_comment"]
    assert "YKM-Curator-Run: run-pr-repair" in report.pr_repair_results[0]["review_request_comment"]
    assert any(probe.name == "pr-repair" and probe.status == "pass" for probe in report.probes)
    markdown = (tmp_path / "output" / "run-report.md").read_text(encoding="utf-8")
    assert "## PR Repair Results" in markdown
    assert "PR `#5`: `validated`" in markdown


def test_pr_repair_handoff_posts_comment_dismisses_reviews_resolves_threads_and_labels(
    tmp_path: Path,
) -> None:
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps({"schema_version": "1", "reachable": True}) + "\n",
        encoding="utf-8",
    )
    repair = PrRepairResult(
        pr_number=5,
        branch="curator/run/upload",
        pr_state="changes_requested",
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        status="pushed",
        message="pushed",
        changed_files=[".ykm/corpus-policy.yaml", "preferences/dev-environment.md"],
        repair_head_sha="abc123repair",
        review_request_comment=_review_request_comment(
            CuratorPrReconciliation(
                pr_number=5,
                pr_state="changes_requested",
                branch="curator/run/upload",
                run_id="run",
                reason="needs work",
            ),
            changed_files=[".ykm/corpus-policy.yaml", "preferences/dev-environment.md"],
        ),
        review_request_comment_status="pending",
        pushed=True,
    )
    snapshot = CuratorPrSnapshot(
        number=5,
        state="open",
        body="YKM-Curator-Run: run\n",
        branch="curator/run/upload",
        labels=["ym-curator: needs work"],
        reviews=[
            CuratorPrReviewSnapshot(
                database_id=123,
                state="CHANGES_REQUESTED",
                author_login="grubbyhacker",
            )
        ],
        review_threads=[
            CuratorPrReviewThreadSnapshot(
                id="PRRT_123",
                is_resolved=False,
                path="preferences/dev-environment.md",
            )
        ],
    )

    results = _complete_pr_repair_handoffs(
        config=CuratorDryRunConfig(
            run_id="run",
            intake=tmp_path / "intake",
            output=tmp_path / "output",
            broker_fixture=broker_fixture,
        ),
        results=[repair],
        snapshots=[snapshot],
    )

    assert [result.operation for result in results] == [
        "issue.comment",
        "pull.review.dismiss",
        "pull.review_thread.resolve",
        "issue.label.add",
        "issue.label.remove",
    ]
    assert all(result.status == "simulated" for result in results)
    assert results[0].idempotency_key == "pr-repair-comment:5:abc123repair"
    assert results[1].idempotency_key == "pr-repair-dismiss-review:5:abc123repair:123"
    assert results[2].idempotency_key == "pr-repair-resolve-thread:5:abc123repair:PRRT_123"
    assert "YKM-Curator-Run: run" in repair.review_request_comment
    assert repair.review_request_comment_status == "posted"
    assert repair.dismissed_review_count == 1
    assert repair.resolved_thread_count == 1
    assert repair.label_update_count == 2


def test_pr_repair_handoff_posts_workflow_blocker_without_review_or_label_mutations(
    tmp_path: Path,
) -> None:
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps({"schema_version": "1", "reachable": True}) + "\n",
        encoding="utf-8",
    )
    repair = PrRepairResult(
        pr_number=18,
        branch="curator/run/upload",
        pr_state="changes_requested",
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        status="validation_failed",
        message="pushed semantic changes but workflow filter blocks validation",
        changed_files=[".ykm/corpus-policy.yaml", "dev/dev-environment.md"],
        repair_head_sha="abc123repair",
        validation_returncode=1,
        validation_stdout_tail=(
            "Corpus validation: 1 error(s), 0 warning(s)\n\nErrors:\n"
            "- .github/workflows/production-index-artifact.yml: workflow path filters "
            "do not cover corpus root: dev\n"
        ),
        review_request_comment=(
            "Curator repair applied the semantic changes, but validation is blocked by a "
            "workflow permission issue.\n\nYKM-Curator-Run: run\n"
        ),
        review_request_comment_status="pending",
        pushed=True,
    )
    snapshot = CuratorPrSnapshot(
        number=18,
        state="open",
        body="YKM-Curator-Run: run\n",
        branch="curator/run/upload",
        labels=["ym-curator: needs work"],
        reviews=[
            CuratorPrReviewSnapshot(
                database_id=123,
                state="CHANGES_REQUESTED",
                author_login="grubbyhacker",
            )
        ],
        review_threads=[
            CuratorPrReviewThreadSnapshot(
                id="PRRT_123",
                is_resolved=False,
                path="preferences/dev-environment.md",
            )
        ],
    )

    results = _complete_pr_repair_handoffs(
        config=CuratorDryRunConfig(
            run_id="run",
            intake=tmp_path / "intake",
            output=tmp_path / "output",
            broker_fixture=broker_fixture,
        ),
        results=[repair],
        snapshots=[snapshot],
    )

    assert [result.operation for result in results] == ["issue.comment"]
    assert results[0].status == "simulated"
    assert repair.review_request_comment_status == "posted"
    assert repair.dismissed_review_count == 0
    assert repair.resolved_thread_count == 0
    assert repair.label_update_count == 0


def test_pr_repair_handoff_stops_when_comment_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_comment_kwargs: dict[str, object] = {}

    class FailingCommentAdapter:
        def add_issue_comment(self, **kwargs) -> ExecutionResult:
            captured_comment_kwargs.update(kwargs)
            return ExecutionResult(
                action_id=kwargs["action_id"],
                operation="issue.comment",
                idempotency_key=kwargs["idempotency_key"],
                status="failed",
                target_repo=kwargs["target_repo"],
                pr_number=kwargs["issue_number"],
                message="comment failed",
            )

        def dismiss_pull_review(self, **_kwargs) -> ExecutionResult:
            raise AssertionError("handoff should not dismiss reviews after comment failure")

        def resolve_review_thread(self, **_kwargs) -> ExecutionResult:
            raise AssertionError("handoff should not resolve threads after comment failure")

        def add_issue_label(self, **_kwargs) -> ExecutionResult:
            raise AssertionError("handoff should not add labels after comment failure")

        def remove_issue_label(self, **_kwargs) -> ExecutionResult:
            raise AssertionError("handoff should not remove labels after comment failure")

    monkeypatch.setattr(
        "curator.runner.FixtureBrokerAdapter.from_path",
        lambda _path: FailingCommentAdapter(),
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps({"schema_version": "1", "reachable": True}) + "\n",
        encoding="utf-8",
    )
    repair = PrRepairResult(
        pr_number=5,
        branch="curator/run/upload",
        pr_state="changes_requested",
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        status="pushed",
        message="pushed",
        changed_files=["preferences/dev-environment.md"],
        repair_head_sha="abc123repair",
        review_request_comment="Curator repair completed and this PR is ready for review again.",
        review_request_comment_status="pending",
        pushed=True,
    )
    snapshot = CuratorPrSnapshot(
        number=5,
        state="open",
        body="YKM-Curator-Run: run\n",
        branch="curator/run/upload",
        labels=["ym-curator: needs work"],
        reviews=[CuratorPrReviewSnapshot(database_id=123, state="CHANGES_REQUESTED")],
        review_threads=[CuratorPrReviewThreadSnapshot(id="PRRT_123", is_resolved=False)],
    )

    results = _complete_pr_repair_handoffs(
        config=CuratorDryRunConfig(
            run_id="run",
            intake=tmp_path / "intake",
            output=tmp_path / "output",
            broker_fixture=broker_fixture,
        ),
        results=[repair],
        snapshots=[snapshot],
    )

    assert len(results) == 1
    assert results[0].operation == "issue.comment"
    assert results[0].status == "failed"
    assert results[0].idempotency_key == "pr-repair-comment:5:abc123repair"
    assert captured_comment_kwargs["metadata"] == {
        "YKM-Curator-Action": "repair",
        "YKM-Curator-PR": "5",
        "YKM-Curator-Run": "run",
    }
    assert repair.review_request_comment_status == "failed"
    assert repair.review_request_comment_message == "comment failed"
    assert repair.dismissed_review_count == 0
    assert repair.resolved_thread_count == 0
    assert repair.label_update_count == 0


def test_runner_retries_pending_pr_repair_handoff_and_deletes_outbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    (intake / "feedback").mkdir(parents=True)
    (intake / "feedback" / "feedback.jsonl").write_text("", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-pr-repair-retry",
                "mode": "manual_live",
                "enabled_actions": ["reconcile", "repair_prs"],
                "pr_repair_executor": "fixture",
                "pr_repair_max_per_run": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    broker_fixture = tmp_path / "broker-fixture.json"
    broker_fixture.write_text(
        json.dumps({"schema_version": "1", "reachable": True, "pr_snapshots": []}) + "\n",
        encoding="utf-8",
    )
    pending = PrRepairResult(
        pr_number=5,
        branch="curator/run/upload",
        pr_state="changes_requested",
        executor="codex_proxy",
        model="ykm-codex-gpt-5-mini",
        status="pushed",
        message="pending handoff retry",
        changed_files=["preferences/dev-environment.md"],
        repair_head_sha="abc123repair",
        review_request_comment="Curator repair completed and this PR is ready for review again.",
        review_request_comment_status="pending",
        pushed=True,
    )
    _write_pending_pr_repair_handoffs(intake, [pending])
    pending_path = intake / "pr-repair-handoffs" / "pending" / "pr-5-abc123repair.json"
    assert pending_path.exists()
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
    assert report.pr_repair_result_count == 1
    assert report.pr_repair_results[0]["review_request_comment_status"] == "posted"
    handoff_results = [
        result for result in report.simulated_execution_results if result["operation"] == "issue.comment"
    ]
    assert len(handoff_results) == 1
    assert not pending_path.exists()


def test_runner_codex_proxy_repair_requires_proxy_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "intake"
    (intake / "feedback").mkdir(parents=True)
    (intake / "feedback" / "feedback.jsonl").write_text("", encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-pr-repair",
                "mode": "dry_run",
                "enabled_actions": ["reconcile", "repair_prs"],
                "pr_repair_executor": "codex_proxy",
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
                        "number": 5,
                        "state": "open",
                        "body": "YKM-Curator-Run: run-pr-repair\n",
                        "branch": "curator/run-pr-repair/upload-upl-20260606",
                        "labels": ["ym-curator: needs work"],
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
            broker_url="http://broker:8080",
            broker_fixture=broker_fixture,
        )
    )

    assert report.status == "fail"
    assert report.pr_repair_result_count == 1
    assert report.pr_repair_results[0]["status"] == "executor_failed"
    assert "Codex proxy base URL and token" in report.pr_repair_results[0]["message"]
    assert any(probe.name == "model-proxy" and probe.status == "fail" for probe in report.probes)


