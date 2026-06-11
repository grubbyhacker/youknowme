from __future__ import annotations

from curator.models import CuratorPrState


TERMINAL_PR_STATES: frozenset[str] = frozenset({"merged", "closed_unmerged"})

ALLOWED_PR_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("open_waiting_review", "commented_needs_triage"),
        ("open_waiting_review", "changes_requested"),
        ("open_waiting_review", "checks_failed"),
        ("open_waiting_review", "checks_missing"),
        ("open_waiting_review", "merged"),
        ("open_waiting_review", "closed_unmerged"),
        ("commented_needs_triage", "changes_requested"),
        ("commented_needs_triage", "ready_for_owner"),
        ("commented_needs_triage", "stale_or_blocked"),
        ("commented_needs_triage", "merged"),
        ("commented_needs_triage", "closed_unmerged"),
        ("changes_requested", "ready_for_owner"),
        ("changes_requested", "merged"),
        ("changes_requested", "closed_unmerged"),
        ("checks_failed", "ready_for_owner"),
        ("checks_failed", "merged"),
        ("checks_failed", "closed_unmerged"),
        ("checks_missing", "ready_for_owner"),
        ("checks_missing", "merged"),
        ("checks_missing", "closed_unmerged"),
        ("ready_for_owner", "open_waiting_review"),
        ("ready_for_owner", "merged"),
        ("ready_for_owner", "closed_unmerged"),
        ("stale_or_blocked", "ready_for_owner"),
        ("stale_or_blocked", "merged"),
        ("stale_or_blocked", "closed_unmerged"),
    }
)


class PrStateTransitionError(ValueError):
    pass


def pr_transition_allowed(current: CuratorPrState, desired: CuratorPrState) -> bool:
    return (current, desired) in ALLOWED_PR_TRANSITIONS


def validate_pr_transition(current: CuratorPrState, desired: CuratorPrState) -> None:
    if current == desired:
        return
    if current in TERMINAL_PR_STATES:
        raise PrStateTransitionError(f"terminal PR state cannot transition: {current} -> {desired}")
    if not pr_transition_allowed(current, desired):
        raise PrStateTransitionError(f"invalid PR state transition: {current} -> {desired}")
