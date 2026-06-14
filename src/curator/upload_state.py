from __future__ import annotations

from datetime import UTC, datetime

from curator.models import (
    UploadCuratorMetadata,
    UploadDecision,
    UploadLogicalState,
    UploadReentryTrigger,
)


ALLOWED_UPLOAD_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("pending", "claimed"),
        ("pending", "deferred"),
        ("pending", "processed"),
        ("claimed", "pr_opened"),
        ("claimed", "deferred"),
        ("claimed", "rejected"),
        ("pr_opened", "processed"),
        ("pr_opened", "deferred"),
        ("pr_opened", "rejected"),
        ("processed", "archived"),
        ("rejected", "archived"),
        ("deferred", "claimed"),
    }
)


class UploadStateTransitionError(ValueError):
    pass


def upload_transition_allowed(current: UploadLogicalState, desired: UploadLogicalState) -> bool:
    return (current, desired) in ALLOWED_UPLOAD_TRANSITIONS


def validate_upload_transition(
    current: UploadLogicalState,
    desired: UploadLogicalState,
) -> None:
    if current == desired:
        return
    if not upload_transition_allowed(current, desired):
        raise UploadStateTransitionError(f"invalid upload state transition: {current} -> {desired}")


def transition_upload_metadata(
    metadata: UploadCuratorMetadata,
    *,
    desired_state: UploadLogicalState,
    run_id: str,
    decision: UploadDecision | None = None,
    branch: str | None = None,
    pr_number: int | None = None,
    issue_number: int | None = None,
    blocking_issue_number: int | None = None,
    reentry_trigger: UploadReentryTrigger | None = None,
    retry_after: datetime | None = None,
    blocking_reason: str | None = None,
    notes: str | None = None,
    timestamp: datetime | None = None,
) -> UploadCuratorMetadata:
    validate_upload_transition(metadata.state, desired_state)
    now = timestamp or datetime.now(UTC)
    return metadata.model_copy(
        update={
            "state": desired_state,
            "decision": decision if decision is not None else metadata.decision,
            "run_id": run_id,
            "branch": branch if branch is not None else metadata.branch,
            "pr_number": pr_number if pr_number is not None else metadata.pr_number,
            "issue_number": issue_number if issue_number is not None else metadata.issue_number,
            "blocking_issue_number": blocking_issue_number
            if blocking_issue_number is not None
            else metadata.blocking_issue_number,
            "claimed_at": now if metadata.state == "pending" and desired_state == "claimed" else metadata.claimed_at,
            "last_checked_at": now,
            "last_action_at": now,
            "reentry_trigger": reentry_trigger
            if reentry_trigger is not None
            else metadata.reentry_trigger,
            "retry_after": retry_after if retry_after is not None else metadata.retry_after,
            "blocking_reason": blocking_reason
            if blocking_reason is not None
            else metadata.blocking_reason,
            "notes": notes if notes is not None else metadata.notes,
        }
    )
