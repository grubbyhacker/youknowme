from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from curator.models import (
    DEFAULT_TARGET_REPO,
    ExecutionIntent,
    FeedbackDecision,
    FeedbackDecisionPreview,
    FeedbackDecisionValue,
    FeedbackPlan,
    PolicyDecision,
    ProposedAction,
)
from curator.body import draft_action_body
from curator.planning import deterministic_branch_name


DEFAULT_OWNER_ASSIGNEE = "grubbyhacker"
BASE_ISSUE_LABELS = ["ykm-curator"]
ISSUE_LABELS_BY_CLASSIFICATION = {
    "corpus_candidate": ["feedback", "corpus"],
    "corpus_issue": ["feedback", "corpus"],
}
STATE_ONLY_DECISIONS = {
    ("no_action", "positive"): "no_action_positive",
    ("no_action", "non_actionable"): "no_action_non_actionable",
    ("no_action", "insufficient_evidence"): "no_action_insufficient_evidence",
    ("defer", "capacity"): "capacity_deferred",
    ("link_to_upload", "upload_linked"): "linked_to_upload",
}


def state_only_feedback_decisions(run_id: str, plan: FeedbackPlan) -> list[FeedbackDecision]:
    decisions: list[FeedbackDecision] = []
    timestamp = datetime.now(UTC)
    for action in plan.proposed_actions:
        decision_value = STATE_ONLY_DECISIONS.get((action.action_type, action.classification))
        if decision_value is None:
            continue
        for feedback_id in action.evidence.feedback_ids:
            decisions.append(
                FeedbackDecision(
                    feedback_id=feedback_id,
                    run_id=run_id,
                    plan_action_id=action.action_id,
                    decision=cast(FeedbackDecisionValue, decision_value),
                    upload_id=action.evidence.upload_ids[0] if action.evidence.upload_ids else None,
                    source_id=action.evidence.source_ids[0] if action.evidence.source_ids else None,
                    section_id=action.evidence.section_ids[0]
                    if action.evidence.section_ids
                    else None,
                    reentry_trigger="next_run" if decision_value == "capacity_deferred" else None,
                    reason=_decision_reason(action),
                    timestamp=timestamp,
                )
            )
    return decisions


def append_feedback_decisions(path: Path, decisions: list[FeedbackDecision]) -> int:
    if not decisions:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(decision.model_dump_json() + "\n")
    return len(decisions)


def reconciliation_feedback_decisions(
    run_id: str,
    previews: list[FeedbackDecisionPreview],
) -> list[FeedbackDecision]:
    timestamp = datetime.now(UTC)
    decisions: list[FeedbackDecision] = []
    for preview in previews:
        if preview.validation != "accepted":
            continue
        if preview.from_decision == preview.to_decision:
            continue
        decisions.append(
            FeedbackDecision(
                feedback_id=preview.feedback_id,
                run_id=run_id,
                plan_action_id="reconciliation",
                decision=preview.to_decision,
                pr_number=preview.pr_number,
                issue_number=preview.issue_number,
                reason=preview.reason,
                timestamp=timestamp,
            )
        )
    return decisions


def reconciliation_feedback_reentry_decisions(
    run_id: str,
    previews: list[FeedbackDecisionPreview],
) -> list[FeedbackDecision]:
    timestamp = datetime.now(UTC)
    decisions: list[FeedbackDecision] = []
    for preview in previews:
        if preview.validation != "accepted":
            continue
        if preview.to_decision != "deferred":
            continue
        decisions.append(
            FeedbackDecision(
                feedback_id=preview.feedback_id,
                run_id=run_id,
                plan_action_id="reconciliation",
                decision="deferred",
                issue_number=preview.issue_number,
                reentry_trigger="next_run",
                reason=preview.reason,
                timestamp=timestamp,
            )
        )
    return decisions


def build_execution_intents(
    run_id: str,
    actions: list[ProposedAction],
    policy_decisions: list[PolicyDecision],
    feedback_records: list[dict[str, object]] | None = None,
) -> list[ExecutionIntent]:
    decisions_by_action = {decision.action_id: decision for decision in policy_decisions}
    feedback_records_by_id = _feedback_records_by_id(feedback_records or [])
    intents: list[ExecutionIntent] = []
    for action in actions:
        decision = decisions_by_action.get(action.action_id)
        if decision is None or decision.status != "allowed":
            continue
        if action.action_type in {"issue", "corpus_issue"}:
            if action.target_repo is None:
                continue
            intents.append(
                ExecutionIntent(
                    action_id=action.action_id,
                    operation="issue.create",
                    idempotency_key=action.idempotency_key,
                    target_repo=action.target_repo,
                    evidence=action.evidence,
                    title=_intent_title(action),
                    body=_intent_body(run_id, action, feedback_records_by_id),
                    labels=_issue_labels(action),
                    assignees=_issue_assignees(action),
                )
            )
        elif action.action_type == "corpus_pr":
            if action.target_repo is None:
                continue
            intents.append(
                ExecutionIntent(
                    action_id=action.action_id,
                    operation="pull.create",
                    idempotency_key=action.idempotency_key,
                    target_repo=action.target_repo,
                    branch=deterministic_branch_name(run_id, action),
                    evidence=action.evidence,
                    title=_intent_title(action),
                    body=_intent_body(run_id, action, feedback_records_by_id),
                )
            )
    return intents


def _intent_title(action: ProposedAction) -> str:
    evidence = action.evidence.feedback_ids or action.evidence.upload_ids or action.evidence.source_ids
    suffix = ", ".join(evidence[:3]) if evidence else action.action_id
    return f"YouKnowMe Curator {action.classification}: {suffix}"


def _issue_labels(action: ProposedAction) -> list[str]:
    labels = [*BASE_ISSUE_LABELS, *ISSUE_LABELS_BY_CLASSIFICATION.get(action.classification, [])]
    return list(dict.fromkeys(labels))


def _issue_assignees(action: ProposedAction) -> list[str]:
    _ = action
    return []


def _intent_body(
    run_id: str,
    action: ProposedAction,
    feedback_records_by_id: dict[str, dict[str, object]],
) -> str:
    if action.target_repo != DEFAULT_TARGET_REPO:
        return draft_action_body(run_id, action)
    return draft_action_body(
        run_id,
        action,
        feedback_records=[
            feedback_records_by_id[feedback_id]
            for feedback_id in action.evidence.feedback_ids
            if feedback_id in feedback_records_by_id
        ],
    )


def _feedback_records_by_id(
    feedback_records: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    records_by_id: dict[str, dict[str, object]] = {}
    for record in feedback_records:
        feedback_id = record.get("feedback_id")
        if isinstance(feedback_id, str):
            records_by_id[feedback_id] = record
    return records_by_id


def _decision_reason(action: ProposedAction) -> str:
    if action.classification == "positive":
        return "Deterministic state-only pass recorded positive feedback as no action."
    if action.classification == "non_actionable":
        return "Deterministic state-only pass recorded non-actionable feedback as no action."
    if action.classification == "capacity":
        return "Deterministic state-only pass deferred feedback after soft action threshold."
    if action.classification == "upload_linked":
        return "Deterministic state-only pass linked feedback to upload processing."
    return "Deterministic state-only pass found insufficient evidence for an action."
