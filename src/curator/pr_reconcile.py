from __future__ import annotations

from curator.markers import parse_curator_markers
from curator.models import CuratorPrReconciliation, CuratorPrSnapshot


CURATOR_NEEDS_WORK_LABEL = "ym-curator: needs work"
CURATOR_WAITING_REVIEW_LABEL = "ym-curator: waiting-review"


def reconcile_pr_snapshots(
    snapshots: list[CuratorPrSnapshot],
) -> list[CuratorPrReconciliation]:
    reconciliations: list[CuratorPrReconciliation] = []
    for snapshot in snapshots:
        markers = parse_curator_markers(snapshot.body)
        if not _is_curator_pr(snapshot, markers.run_id):
            continue
        pr_state, reason = _classify_pr(snapshot)
        reconciliations.append(
            CuratorPrReconciliation(
                pr_number=snapshot.number,
                pr_state=pr_state,
                branch=snapshot.branch,
                labels=snapshot.labels,
                run_id=markers.run_id or _run_id_from_branch(snapshot.branch),
                action_id=markers.action_id,
                idempotency_key=markers.idempotency_key,
                upload_ids=markers.upload_ids,
                feedback_ids=markers.feedback_ids,
                source_ids=markers.source_ids,
                section_ids=markers.section_ids,
                result_ids=markers.result_ids,
                reason=reason,
            )
        )
    return reconciliations


def _is_curator_pr(snapshot: CuratorPrSnapshot, marker_run_id: str | None) -> bool:
    if marker_run_id:
        return True
    return bool(snapshot.branch and snapshot.branch.startswith("curator/"))


def _run_id_from_branch(branch: str | None) -> str | None:
    if branch is None:
        return None
    parts = branch.split("/", 2)
    if len(parts) < 3 or parts[0] != "curator":
        return None
    return parts[1] or None


def _classify_pr(snapshot: CuratorPrSnapshot) -> tuple[str, str]:
    if snapshot.state == "merged":
        return "merged", "PR is merged."
    if snapshot.state == "closed":
        return "closed_unmerged", "PR is closed without a merge marker."
    if _has_needs_work_label(snapshot):
        return "changes_requested", f"PR has `{CURATOR_NEEDS_WORK_LABEL}` label."
    if _has_waiting_review_label(snapshot):
        return "ready_for_owner", f"PR has `{CURATOR_WAITING_REVIEW_LABEL}` label."
    if snapshot.review_decision == "changes_requested":
        return "changes_requested", "Latest review decision requests changes."
    if snapshot.unresolved_thread_count > 0:
        return "commented_needs_triage", "Unresolved review threads require triage."
    if snapshot.checks_conclusion == "failure":
        return "checks_failed", "Latest check conclusion failed."
    if snapshot.checks_conclusion == "missing":
        return "checks_missing", "Expected validation checks are missing for this Curator PR."
    if snapshot.review_decision in {"approved", "commented"}:
        return "ready_for_owner", "PR has reviewer activity and is waiting for owner."
    return "open_waiting_review", "Open PR has no blocking review, comments, or failed checks."


def _has_needs_work_label(snapshot: CuratorPrSnapshot) -> bool:
    return any(label.strip().lower() == CURATOR_NEEDS_WORK_LABEL for label in snapshot.labels)


def _has_waiting_review_label(snapshot: CuratorPrSnapshot) -> bool:
    return any(label.strip().lower() == CURATOR_WAITING_REVIEW_LABEL for label in snapshot.labels)
