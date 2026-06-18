from __future__ import annotations

from curator.models import (
    CuratorExecutionPolicy,
    GithubMutationBudget,
    PolicyDecision,
    ProposedAction,
)


def policy_from_budget(budget: GithubMutationBudget | dict[str, int]) -> CuratorExecutionPolicy:
    if isinstance(budget, GithubMutationBudget):
        data = budget.model_dump()
    else:
        data = budget
    return CuratorExecutionPolicy(
        max_new_objects_per_run=data.get("max_new_objects_per_run", 0),
        upload_new_object_budget=data.get("upload", 0),
        feedback_new_object_budget=data.get("feedback", 0),
    )


def evaluate_feedback_action_policy(
    actions: list[ProposedAction],
    policy: CuratorExecutionPolicy,
) -> list[PolicyDecision]:
    decisions: list[PolicyDecision] = []
    used_total = 0
    used_feedback = 0
    for action in actions:
        if action.action_type not in {"issue", "corpus_issue", "corpus_pr"}:
            decisions.append(_allowed_without_mutation(action))
            continue
        target_repo = action.target_repo
        if action.action_type in {"issue", "corpus_issue"} and (
            target_repo not in policy.allowed_issue_repos
        ):
            decisions.append(_denied(action, "target repository is not issue-allowlisted"))
            continue
        if action.action_type == "corpus_pr" and target_repo not in policy.allowed_pr_repos:
            decisions.append(_denied(action, "target repository is not PR-allowlisted"))
            continue
        if used_total >= policy.max_new_objects_per_run:
            decisions.append(_denied(action, "run GitHub mutation budget exhausted"))
            continue
        if used_feedback >= policy.feedback_new_object_budget:
            decisions.append(_denied(action, "feedback GitHub mutation budget exhausted"))
            continue
        used_total += 1
        used_feedback += 1
        decisions.append(
            PolicyDecision(
                action_id=action.action_id,
                action_type=action.action_type,
                idempotency_key=action.idempotency_key,
                status="allowed",
                reason="action passes deterministic feedback mutation policy",
                target_repo=target_repo,
                budget_bucket="feedback",
            )
        )
    return decisions


def _allowed_without_mutation(action: ProposedAction) -> PolicyDecision:
    return PolicyDecision(
        action_id=action.action_id,
        action_type=action.action_type,
        idempotency_key=action.idempotency_key,
        status="allowed",
        reason="action does not create a GitHub object",
        target_repo=action.target_repo,
        budget_bucket="none",
    )


def _denied(action: ProposedAction, reason: str) -> PolicyDecision:
    return PolicyDecision(
        action_id=action.action_id,
        action_type=action.action_type,
        idempotency_key=action.idempotency_key,
        status="denied",
        reason=reason,
        target_repo=action.target_repo,
        budget_bucket="feedback",
    )
