from __future__ import annotations

from datetime import datetime


from curator.models import (
    CuratorIssueSnapshot,
    CuratorPrReviewCommentSnapshot,
    CuratorPrReviewThreadSnapshot,
    CuratorPrSnapshot,
    FeedbackDecision,
    FeedbackPlan,
    FeedbackWindow,
    UploadBundleSnapshot,
    UploadQueueSnapshot,
    UploadCuratorMetadata,
)
from curator.reconcile import build_reconciliation_summary





def test_reconciliation_summary_includes_pr_marker_reconciliations() -> None:
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={},
        feedback_plan=FeedbackPlan(
            run_id="run-pr",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(counts={}),
        pr_snapshots=[
            CuratorPrSnapshot(
                number=44,
                state="closed",
                body="YKM-Curator-Run: run-pr\nYKM-Curator-Feedback: fb_2\n",
                branch="curator/run-pr/corpus-pr-fb-2",
            )
        ],
    )

    assert summary.pr_reconciliation_count == 1
    assert summary.pr_state_counts == {"closed_unmerged": 1}
    assert summary.pr_reconciliations[0].pr_state == "closed_unmerged"
    assert summary.pr_reconciliations[0].feedback_ids == ["fb_2"]


def test_reconciliation_summary_previews_upload_transition_from_merged_pr() -> None:
    metadata = UploadCuratorMetadata(
        upload_id="upl_1",
        state="pr_opened",
        run_id="old-run",
        pr_number=44,
    )
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={},
        feedback_plan=FeedbackPlan(
            run_id="run-pr",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(
            counts={"claimed": 1},
            bundles=[
                UploadBundleSnapshot(
                    upload_id="upl_1",
                    queue="claimed",
                    path="/data/intake/uploads/claimed/upl_1",
                    has_manifest=True,
                    has_curator_metadata=True,
                    curator_metadata=metadata,
                )
            ],
        ),
        pr_snapshots=[
            CuratorPrSnapshot(
                number=44,
                state="merged",
                body="YKM-Curator-Run: run-pr\nYKM-Curator-Upload: upl_1\n",
                branch="curator/run-pr/upload-upl-1",
            )
        ],
    )

    assert metadata.state == "pr_opened"
    assert summary.upload_transition_preview_count == 1
    preview = summary.upload_transition_previews[0]
    assert preview.upload_id == "upl_1"
    assert preview.from_state == "pr_opened"
    assert preview.to_state == "processed"
    assert preview.validation == "accepted"


def test_reconciliation_summary_previews_feedback_decisions_from_terminal_prs() -> None:
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={
            "fb_merged": FeedbackDecision(
                feedback_id="fb_merged",
                run_id="old-run",
                plan_action_id="act_old",
                decision="pr_opened",
                pr_number=44,
                reason="old PR",
                timestamp=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
            ),
            "fb_conflict": FeedbackDecision(
                feedback_id="fb_conflict",
                run_id="old-run",
                plan_action_id="act_old",
                decision="no_action_positive",
                reason="already closed as positive",
                timestamp=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
            ),
        },
        feedback_plan=FeedbackPlan(
            run_id="run-pr",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(counts={}),
        pr_snapshots=[
            CuratorPrSnapshot(
                number=44,
                state="merged",
                body="YKM-Curator-Run: run-pr\nYKM-Curator-Feedback: fb_merged\n",
                branch="curator/run-pr/corpus-pr-fb-merged",
            ),
            CuratorPrSnapshot(
                number=45,
                state="closed",
                body=(
                    "YKM-Curator-Run: run-pr\n"
                    "YKM-Curator-Feedback: fb_closed\n"
                    "YKM-Curator-Feedback: fb_conflict\n"
                ),
                branch="curator/run-pr/corpus-pr-fb-closed",
            ),
        ],
    )

    assert summary.feedback_decision_preview_count == 3
    previews = {preview.feedback_id: preview for preview in summary.feedback_decision_previews}
    assert previews["fb_merged"].from_decision == "pr_opened"
    assert previews["fb_merged"].to_decision == "pr_opened"
    assert previews["fb_merged"].validation == "accepted"
    assert previews["fb_closed"].from_decision is None
    assert previews["fb_closed"].to_decision == "deferred"
    assert previews["fb_closed"].validation == "accepted"
    assert previews["fb_conflict"].validation == "rejected"
    assert "should not be overwritten" in previews["fb_conflict"].reason


def test_reconciliation_summary_extracts_owner_review_guidance_candidates() -> None:
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={},
        feedback_plan=FeedbackPlan(
            run_id="run-pr",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(counts={}),
        pr_snapshots=[
            CuratorPrSnapshot(
                number=19,
                state="closed",
                body="YKM-Curator-Run: run-pr\nYKM-Curator-Upload: upl_1\n",
                branch="curator/run-pr/upload-upl-1",
                review_threads=[
                    CuratorPrReviewThreadSnapshot(
                        path=".ykm/corpus-policy.yaml.bak",
                        line=1,
                        comments=[
                            CuratorPrReviewCommentSnapshot(
                                database_id=123,
                                author_login="grubbyhacker",
                                body=(
                                    "Never, ever create a backup file in git. "
                                    "Not in this repo, not anywhere."
                                ),
                                path=".ykm/corpus-policy.yaml.bak",
                                line=1,
                            ),
                            CuratorPrReviewCommentSnapshot(
                                database_id=124,
                                author_login="someone-else",
                                body="This should not become owner guidance.",
                            ),
                        ],
                    )
                ],
            )
        ],
    )

    assert summary.review_guidance_candidate_count == 1
    candidate = summary.review_guidance_candidates[0]
    assert candidate.pr_number == 19
    assert candidate.path == ".ykm/corpus-policy.yaml.bak"
    assert candidate.line == 1
    assert candidate.comment_id == "123"
    assert "Never, ever create a backup file in git" in candidate.guidance


def test_reconciliation_matches_markerless_branch_pr_to_local_state() -> None:
    metadata = UploadCuratorMetadata(
        upload_id="upl_branch_only",
        state="pr_opened",
        run_id="old-run",
        branch="curator/run-branch-only/upload-upl-branch-only-abc123",
        pr_number=51,
    )
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={
            "fb_branch_only": FeedbackDecision(
                feedback_id="fb_branch_only",
                run_id="old-run",
                plan_action_id="act_old",
                decision="pr_opened",
                pr_number=51,
                reason="old PR",
                timestamp=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
            )
        },
        feedback_plan=FeedbackPlan(
            run_id="run-branch-only",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(
            counts={"claimed": 1},
            bundles=[
                UploadBundleSnapshot(
                    upload_id="upl_branch_only",
                    queue="claimed",
                    path="/data/intake/uploads/claimed/upl_branch_only",
                    has_manifest=True,
                    has_curator_metadata=True,
                    curator_metadata=metadata,
                )
            ],
        ),
        pr_snapshots=[
            CuratorPrSnapshot(
                number=51,
                state="merged",
                body="No marker block in this snapshot.",
                branch="curator/run-branch-only/upload-upl-branch-only-abc123",
            )
        ],
    )

    assert summary.pr_reconciliation_count == 1
    assert summary.pr_reconciliations[0].run_id == "run-branch-only"
    assert summary.pr_reconciliations[0].branch == metadata.branch
    assert summary.upload_transition_preview_count == 1
    upload_preview = summary.upload_transition_previews[0]
    assert upload_preview.upload_id == "upl_branch_only"
    assert upload_preview.to_state == "processed"
    assert upload_preview.validation == "accepted"
    assert summary.feedback_decision_preview_count == 1
    feedback_preview = summary.feedback_decision_previews[0]
    assert feedback_preview.feedback_id == "fb_branch_only"
    assert feedback_preview.to_decision == "pr_opened"
    assert feedback_preview.validation == "accepted"


def test_reconciliation_summary_previews_issue_closure_reentry() -> None:
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={
            "fb_blocked": FeedbackDecision(
                feedback_id="fb_blocked",
                run_id="old-run",
                plan_action_id="act_old",
                decision="deferred",
                issue_number=77,
                reentry_trigger="owner_input_resolved",
                reason="waiting on issue",
                timestamp=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
            ),
            "fb_open": FeedbackDecision(
                feedback_id="fb_open",
                run_id="old-run",
                plan_action_id="act_old",
                decision="deferred",
                issue_number=78,
                reentry_trigger="owner_input_resolved",
                reason="still waiting",
                timestamp=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
            ),
        },
        feedback_plan=FeedbackPlan(
            run_id="run-issue",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(
            counts={"deferred": 1},
            bundles=[
                UploadBundleSnapshot(
                    upload_id="upl_blocked",
                    queue="deferred",
                    path="/data/intake/uploads/deferred/upl_blocked",
                    has_manifest=True,
                    has_curator_metadata=True,
                    curator_metadata=UploadCuratorMetadata(
                        upload_id="upl_blocked",
                        state="deferred",
                        run_id="old-run",
                        blocking_issue_number=77,
                        reentry_trigger="owner_input_resolved",
                    ),
                )
            ],
        ),
        issue_snapshots=[
            CuratorIssueSnapshot(number=77, state="closed"),
            CuratorIssueSnapshot(number=78, state="open"),
        ],
    )

    assert summary.feedback_reentry_preview_count == 1
    feedback_preview = summary.feedback_reentry_previews[0]
    assert feedback_preview.feedback_id == "fb_blocked"
    assert feedback_preview.issue_number == 77
    assert feedback_preview.validation == "accepted"
    assert summary.upload_transition_preview_count == 1
    upload_preview = summary.upload_transition_previews[0]
    assert upload_preview.upload_id == "upl_blocked"
    assert upload_preview.issue_number == 77
    assert upload_preview.from_state == "deferred"
    assert upload_preview.to_state == "claimed"
    assert upload_preview.validation == "accepted"


def test_reconciliation_summary_rejects_invalid_upload_transition_preview() -> None:
    summary = build_reconciliation_summary(
        feedback_records=[],
        latest_decisions={},
        feedback_plan=FeedbackPlan(
            run_id="run-pr",
            feedback_window=FeedbackWindow(start_offset=0, end_offset=0),
            created_at=datetime.fromisoformat("2026-06-08T12:00:00+00:00"),
        ),
        upload_snapshot=UploadQueueSnapshot(
            counts={"claimed": 1},
            bundles=[
                UploadBundleSnapshot(
                    upload_id="upl_1",
                    queue="claimed",
                    path="/data/intake/uploads/claimed/upl_1",
                    has_manifest=True,
                    has_curator_metadata=True,
                    curator_metadata=UploadCuratorMetadata(
                        upload_id="upl_1",
                        state="claimed",
                        run_id="old-run",
                        pr_number=44,
                    ),
                )
            ],
        ),
        pr_snapshots=[
            CuratorPrSnapshot(
                number=44,
                state="merged",
                body="YKM-Curator-Run: run-pr\nYKM-Curator-Upload: upl_1\n",
                branch="curator/run-pr/upload-upl-1",
            )
        ],
    )

    assert summary.upload_transition_previews[0].validation == "rejected"
    assert "claimed -> processed" in summary.upload_transition_previews[0].reason


