from __future__ import annotations

from collections import Counter

from pydantic import ValidationError

from curator.models import (
    BranchCollision,
    BranchPreview,
    CuratorIssueSnapshot,
    FeedbackDecision,
    FeedbackDecisionPreview,
    FeedbackDecisionValue,
    FeedbackInputRecord,
    FeedbackPlan,
    CuratorPrSnapshot,
    CuratorPrReconciliation,
    ReconciliationSummary,
    UploadPlan,
    UploadTransitionPreview,
    UploadQueueSnapshot,
)
from curator.planning import REENTER_DECISIONS, deterministic_branch_name
from curator.pr_reconcile import reconcile_pr_snapshots
from curator.upload_state import UploadStateTransitionError, validate_upload_transition


def build_reconciliation_summary(
    *,
    feedback_records: list[dict[str, object]],
    latest_decisions: dict[str, FeedbackDecision],
    feedback_plan: FeedbackPlan,
    upload_snapshot: UploadQueueSnapshot,
    upload_plan: UploadPlan | None = None,
    pr_snapshots: list[CuratorPrSnapshot] | None = None,
    issue_snapshots: list[CuratorIssueSnapshot] | None = None,
) -> ReconciliationSummary:
    valid_feedback_ids: list[str] = []
    decided = 0
    reentered = 0
    for raw_record in feedback_records:
        try:
            record = FeedbackInputRecord.model_validate(raw_record)
        except ValidationError:
            continue
        valid_feedback_ids.append(record.feedback_id)
        decision = latest_decisions.get(record.feedback_id)
        if decision is None:
            continue
        decided += 1
        if decision.decision in REENTER_DECISIONS:
            reentered += 1

    metadata_state_counts = Counter(
        bundle.curator_metadata.state
        for bundle in upload_snapshot.bundles
        if bundle.curator_metadata is not None
    )
    branch_previews = [
        BranchPreview(
            action_id=action.action_id,
            idempotency_key=action.idempotency_key,
            branch=deterministic_branch_name(feedback_plan.run_id, action),
        )
        for action in feedback_plan.proposed_actions
        if action.action_type in {"corpus_pr", "issue"}
    ]
    if upload_plan is not None:
        branch_previews.extend(
            BranchPreview(
                action_id=preview.action_id,
                idempotency_key=preview.idempotency_key,
                branch=preview.branch,
            )
            for preview in upload_plan.review_previews
        )
    existing_branches = {
        bundle.curator_metadata.branch: bundle.upload_id
        for bundle in upload_snapshot.bundles
        if bundle.curator_metadata is not None and bundle.curator_metadata.branch
    }
    branch_collisions = [
        BranchCollision(
            action_id=preview.action_id,
            branch=preview.branch,
            existing_upload_id=existing_branches[preview.branch],
        )
        for preview in branch_previews
        if preview.branch in existing_branches
    ]
    pr_reconciliations = reconcile_pr_snapshots(pr_snapshots or [])
    pr_state_counts = Counter(reconciliation.pr_state for reconciliation in pr_reconciliations)
    upload_transition_previews = _upload_transition_previews(
        upload_snapshot=upload_snapshot,
        pr_reconciliations=pr_reconciliations,
    )
    issue_upload_transition_previews = _issue_upload_transition_previews(
        upload_snapshot=upload_snapshot,
        issue_snapshots=issue_snapshots or [],
    )
    feedback_decision_previews = _feedback_decision_previews(
        latest_decisions=latest_decisions,
        pr_reconciliations=pr_reconciliations,
    )
    feedback_reentry_previews = _feedback_reentry_previews(
        latest_decisions=latest_decisions,
        issue_snapshots=issue_snapshots or [],
    )
    return ReconciliationSummary(
        feedback_window_record_count=len(valid_feedback_ids),
        decided_feedback_count=decided,
        undecided_feedback_count=len(valid_feedback_ids) - decided,
        reentered_feedback_count=reentered,
        upload_metadata_state_counts=dict(sorted(metadata_state_counts.items())),
        invalid_upload_metadata_count=sum(
            1 for bundle in upload_snapshot.bundles if bundle.metadata_error
        ),
        branch_previews=branch_previews,
        branch_collision_count=len(branch_collisions),
        branch_collisions=branch_collisions[:20],
        pr_reconciliation_count=len(pr_reconciliations),
        pr_state_counts=dict(sorted(pr_state_counts.items())),
        pr_reconciliations=pr_reconciliations[:20],
        upload_transition_preview_count=len(
            upload_transition_previews + issue_upload_transition_previews
        ),
        upload_transition_previews=(
            upload_transition_previews + issue_upload_transition_previews
        )[:20],
        feedback_decision_preview_count=len(feedback_decision_previews),
        feedback_decision_previews=feedback_decision_previews[:20],
        feedback_reentry_preview_count=len(feedback_reentry_previews),
        feedback_reentry_previews=feedback_reentry_previews[:20],
    )


def _upload_transition_previews(
    *,
    upload_snapshot: UploadQueueSnapshot,
    pr_reconciliations: list[CuratorPrReconciliation],
) -> list[UploadTransitionPreview]:
    metadata_by_upload = {
        bundle.upload_id: bundle.curator_metadata
        for bundle in upload_snapshot.bundles
        if bundle.curator_metadata is not None
    }
    metadata_by_pr_number = {
        bundle.curator_metadata.pr_number: bundle.curator_metadata
        for bundle in upload_snapshot.bundles
        if bundle.curator_metadata is not None and bundle.curator_metadata.pr_number is not None
    }
    metadata_by_branch = {
        bundle.curator_metadata.branch: bundle.curator_metadata
        for bundle in upload_snapshot.bundles
        if bundle.curator_metadata is not None and bundle.curator_metadata.branch is not None
    }
    previews: list[UploadTransitionPreview] = []
    for reconciliation in pr_reconciliations:
        if reconciliation.pr_state not in {"merged", "closed_unmerged"}:
            continue
        desired = "processed" if reconciliation.pr_state == "merged" else "deferred"
        candidate_upload_ids = set(reconciliation.upload_ids)
        if reconciliation.pr_number in metadata_by_pr_number:
            candidate_upload_ids.add(metadata_by_pr_number[reconciliation.pr_number].upload_id)
        if reconciliation.branch in metadata_by_branch:
            candidate_upload_ids.add(metadata_by_branch[reconciliation.branch].upload_id)
        for upload_id in sorted(candidate_upload_ids):
            metadata = metadata_by_upload.get(upload_id)
            if metadata is None:
                continue
            try:
                validate_upload_transition(metadata.state, desired)
            except UploadStateTransitionError as exc:
                previews.append(
                    UploadTransitionPreview(
                        upload_id=upload_id,
                        pr_number=reconciliation.pr_number,
                        from_state=metadata.state,
                        to_state=desired,
                        validation="rejected",
                        reason=str(exc),
                    )
                )
                continue
            previews.append(
                UploadTransitionPreview(
                    upload_id=upload_id,
                    pr_number=reconciliation.pr_number,
                    from_state=metadata.state,
                    to_state=desired,
                    validation="accepted",
                    reason=(
                        "Merged Curator PR can mark linked upload processed."
                        if desired == "processed"
                        else "Closed-unmerged Curator PR can defer linked upload."
                    ),
                )
            )
    return previews


def _issue_upload_transition_previews(
    *,
    upload_snapshot: UploadQueueSnapshot,
    issue_snapshots: list[CuratorIssueSnapshot],
) -> list[UploadTransitionPreview]:
    closed_issue_numbers = {snapshot.number for snapshot in issue_snapshots if snapshot.state == "closed"}
    if not closed_issue_numbers:
        return []
    previews: list[UploadTransitionPreview] = []
    for bundle in upload_snapshot.bundles:
        metadata = bundle.curator_metadata
        if metadata is None:
            continue
        if metadata.state != "deferred" or metadata.reentry_trigger != "owner_input_resolved":
            continue
        if metadata.blocking_issue_number not in closed_issue_numbers:
            continue
        try:
            validate_upload_transition(metadata.state, "claimed")
        except UploadStateTransitionError as exc:
            previews.append(
                UploadTransitionPreview(
                    upload_id=metadata.upload_id,
                    issue_number=metadata.blocking_issue_number,
                    from_state=metadata.state,
                    to_state="claimed",
                    validation="rejected",
                    reason=str(exc),
                )
            )
            continue
        previews.append(
            UploadTransitionPreview(
                upload_id=metadata.upload_id,
                issue_number=metadata.blocking_issue_number,
                from_state=metadata.state,
                to_state="claimed",
                validation="accepted",
                reason="Closed blocking issue can re-enter deferred upload for review.",
            )
        )
    return previews


def _feedback_decision_previews(
    *,
    latest_decisions: dict[str, FeedbackDecision],
    pr_reconciliations: list[CuratorPrReconciliation],
) -> list[FeedbackDecisionPreview]:
    previews: list[FeedbackDecisionPreview] = []
    for reconciliation in pr_reconciliations:
        if reconciliation.pr_state not in {"merged", "closed_unmerged"}:
            continue
        desired = _desired_feedback_decision(reconciliation.pr_state)
        candidate_feedback_ids = set(reconciliation.feedback_ids)
        candidate_feedback_ids.update(
            feedback_id
            for feedback_id, decision in latest_decisions.items()
            if decision.pr_number == reconciliation.pr_number
        )
        for feedback_id in sorted(candidate_feedback_ids):
            current = latest_decisions.get(feedback_id)
            validation, reason = _feedback_preview_validation(current, desired, reconciliation)
            previews.append(
                FeedbackDecisionPreview(
                    feedback_id=feedback_id,
                    pr_number=reconciliation.pr_number,
                    from_decision=current.decision if current is not None else None,
                    to_decision=desired,
                    validation=validation,
                    reason=reason,
                )
            )
    return previews


def _feedback_reentry_previews(
    *,
    latest_decisions: dict[str, FeedbackDecision],
    issue_snapshots: list[CuratorIssueSnapshot],
) -> list[FeedbackDecisionPreview]:
    closed_issue_numbers = {snapshot.number for snapshot in issue_snapshots if snapshot.state == "closed"}
    if not closed_issue_numbers:
        return []
    previews: list[FeedbackDecisionPreview] = []
    for feedback_id, decision in latest_decisions.items():
        if decision.decision != "deferred":
            continue
        if decision.reentry_trigger != "owner_input_resolved":
            continue
        if decision.issue_number not in closed_issue_numbers:
            continue
        previews.append(
            FeedbackDecisionPreview(
                feedback_id=feedback_id,
                issue_number=decision.issue_number,
                from_decision=decision.decision,
                to_decision="deferred",
                validation="accepted",
                reason="Closed blocking issue can re-enter deferred feedback for planning.",
            )
        )
    return previews


def _desired_feedback_decision(pr_state: str) -> FeedbackDecisionValue:
    if pr_state == "merged":
        return "pr_opened"
    return "deferred"


def _feedback_preview_validation(
    current: FeedbackDecision | None,
    desired: FeedbackDecisionValue,
    reconciliation: CuratorPrReconciliation,
) -> tuple[str, str]:
    if current is None:
        return "accepted", _feedback_preview_reason(desired, reconciliation)
    if current.decision == desired:
        return "accepted", "Current feedback decision already matches reconciled PR outcome."
    if current.decision in {"deferred", "capacity_deferred", "pr_opened"}:
        return "accepted", _feedback_preview_reason(desired, reconciliation)
    return (
        "rejected",
        f"Current feedback decision {current.decision} should not be overwritten by PR reconciliation.",
    )


def _feedback_preview_reason(
    desired: FeedbackDecisionValue,
    reconciliation: CuratorPrReconciliation,
) -> str:
    if desired == "pr_opened":
        return "Merged Curator PR confirms feedback was handled by a corpus PR."
    return f"Curator PR #{reconciliation.pr_number} closed without merge; feedback can be deferred."
