from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from curator.models import (
    ActionEvidence,
    FeedbackDecision,
    FeedbackInputRecord,
    FeedbackPlan,
    FeedbackWindow,
    DEFAULT_TARGET_REPO,
    ProposedAction,
    UploadBundleSnapshot,
    UploadCuratorMetadata,
    UploadLogicalState,
    UploadPlan,
    UploadQueueSnapshot,
    UploadReviewPreview,
)
from curator.state import deterministic_idempotency_key
from curator.upload_draft import draft_upload_corpus_change


REENTER_DECISIONS = {"deferred", "capacity_deferred"}
IMMEDIATE_REENTRY_TRIGGERS = {"next_run"}


def ready_reentry_feedback_ids(
    latest_decisions: dict[str, FeedbackDecision],
    *,
    now: datetime | None = None,
) -> set[str]:
    current_time = now or datetime.now(UTC)
    ready: set[str] = set()
    for feedback_id, decision in latest_decisions.items():
        if decision.decision == "capacity_deferred":
            ready.add(feedback_id)
            continue
        if decision.decision != "deferred":
            continue
        if decision.reentry_trigger in IMMEDIATE_REENTRY_TRIGGERS:
            ready.add(feedback_id)
        elif decision.reentry_trigger == "retry_after" and decision.retry_after is not None:
            if decision.retry_after <= current_time:
                ready.add(feedback_id)
    return ready


def build_feedback_plan(
    *,
    run_id: str,
    feedback_window: FeedbackWindow,
    feedback_records: list[dict[str, object]],
    latest_decisions: dict[str, FeedbackDecision],
    soft_action_threshold: int = 10,
) -> FeedbackPlan:
    included: list[str] = []
    reentered: list[str] = []
    proposed_actions: list[ProposedAction] = []
    referenced_upload_ids: set[str] = set()
    referenced_source_ids: set[str] = set()
    referenced_section_ids: set[str] = set()
    referenced_result_ids: set[str] = set()
    capacity_deferred_feedback_ids: list[str] = []
    action_index = 1
    github_object_action_count = 0

    for raw_record in feedback_records:
        try:
            record = FeedbackInputRecord.model_validate(raw_record)
        except ValidationError:
            continue
        decision = latest_decisions.get(record.feedback_id)
        if decision is not None and decision.decision not in REENTER_DECISIONS:
            continue
        if decision is not None:
            reentered.append(record.feedback_id)
        included.append(record.feedback_id)
        if record.upload_id:
            referenced_upload_ids.add(record.upload_id)
        if record.source_id:
            referenced_source_ids.add(record.source_id)
        if record.section_id:
            referenced_section_ids.add(record.section_id)
        referenced_result_ids.update(record.result_ids)
        action = _action_for_record(run_id, action_index, record)
        if (
            action.action_type in {"issue", "corpus_pr"}
            and github_object_action_count >= soft_action_threshold
        ):
            capacity_deferred_feedback_ids.append(record.feedback_id)
            proposed_actions.append(_action_for_record(run_id, action_index, record, force_defer=True))
            action_index += 1
            continue
        proposed_actions.append(action)
        if action.action_type in {"issue", "corpus_pr"}:
            github_object_action_count += 1
        action_index += 1
    proposed_actions = _merge_groupable_actions(proposed_actions)

    return FeedbackPlan(
        run_id=run_id,
        feedback_window=feedback_window,
        included_feedback_ids=included,
        reentered_feedback_ids=reentered,
        referenced_upload_ids=sorted(referenced_upload_ids),
        referenced_source_ids=sorted(referenced_source_ids),
        referenced_section_ids=sorted(referenced_section_ids),
        referenced_result_ids=sorted(referenced_result_ids),
        soft_action_threshold=soft_action_threshold,
        capacity_deferred_feedback_ids=capacity_deferred_feedback_ids,
        proposed_actions=proposed_actions,
        created_at=datetime.now(UTC),
    )


def _merge_groupable_actions(actions: list[ProposedAction]) -> list[ProposedAction]:
    merged: list[ProposedAction] = []
    index_by_key: dict[tuple[object, ...], int] = {}
    for action in actions:
        key = _action_group_key(action)
        if key is None:
            merged.append(action)
            continue
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(merged)
            merged.append(action)
            continue
        existing = merged[existing_index]
        evidence = ActionEvidence(
            feedback_ids=sorted(
                set(existing.evidence.feedback_ids) | set(action.evidence.feedback_ids)
            ),
            upload_ids=sorted(set(existing.evidence.upload_ids) | set(action.evidence.upload_ids)),
            source_ids=sorted(set(existing.evidence.source_ids) | set(action.evidence.source_ids)),
            section_ids=sorted(
                set(existing.evidence.section_ids) | set(action.evidence.section_ids)
            ),
            result_ids=sorted(set(existing.evidence.result_ids) | set(action.evidence.result_ids)),
        )
        merged[existing_index] = existing.model_copy(
            update={
                "evidence": evidence,
                "idempotency_key": deterministic_idempotency_key(existing.action_type, evidence),
            }
        )
    return merged


def _action_group_key(action: ProposedAction) -> tuple[object, ...] | None:
    if action.evidence.upload_ids:
        return (
            action.action_type,
            action.classification,
            action.target_repo,
            "upload",
            tuple(sorted(action.evidence.upload_ids)),
        )
    if action.evidence.source_ids:
        return (
            action.action_type,
            action.classification,
            action.target_repo,
            "source",
            tuple(sorted(action.evidence.source_ids)),
        )
    if action.evidence.section_ids:
        return (
            action.action_type,
            action.classification,
            action.target_repo,
            "section",
            tuple(sorted(action.evidence.section_ids)),
        )
    return None


def deterministic_branch_name(run_id: str, action: ProposedAction) -> str:
    evidence_ids = (
        action.evidence.feedback_ids
        or action.evidence.upload_ids
        or action.evidence.source_ids
        or action.evidence.section_ids
        or action.evidence.result_ids
        or ["none"]
    )
    evidence_slug = _slug("-".join(evidence_ids), fallback="evidence")
    action_slug = action.action_type.replace("_", "-")
    short_id = action.idempotency_key.rsplit(":", maxsplit=1)[-1][:12]
    return f"curator/{run_id}/{action_slug}-{evidence_slug}-{short_id}"


def build_upload_plan(
    *,
    run_id: str,
    upload_snapshot: UploadQueueSnapshot,
) -> UploadPlan:
    included_upload_ids: list[str] = []
    review_previews: list[UploadReviewPreview] = []
    proposed_actions: list[ProposedAction] = []
    action_index = 1
    for bundle in upload_snapshot.bundles:
        if not _upload_needs_deterministic_review(bundle):
            continue
        included_upload_ids.append(bundle.upload_id)
        evidence = ActionEvidence(upload_ids=[bundle.upload_id])
        action_type = "defer"
        action_id = f"upl_act_{action_index}"
        idempotency_key = deterministic_idempotency_key(action_type, evidence)
        proposed_actions.append(
            ProposedAction(
                action_id=action_id,
                action_type=action_type,
                classification="upload_review_pending",
                idempotency_key=idempotency_key,
                evidence=evidence,
                validation="accepted",
                execution="not_executed",
            )
        )
        review_previews.append(
            _upload_review_preview(
                run_id=run_id,
                action_id=action_id,
                bundle=bundle,
            )
        )
        action_index += 1
    return UploadPlan(
        run_id=run_id,
        included_upload_ids=included_upload_ids,
        review_previews=review_previews,
        proposed_actions=proposed_actions,
        created_at=datetime.now(UTC),
    )


def _upload_review_preview(
    *,
    run_id: str,
    action_id: str,
    bundle: UploadBundleSnapshot,
) -> UploadReviewPreview:
    current_state = bundle.curator_metadata.state if bundle.curator_metadata else _state_from_queue(bundle)
    evidence = ActionEvidence(upload_ids=[bundle.upload_id])
    idempotency_key = deterministic_idempotency_key("upload", evidence)
    draft = draft_upload_corpus_change(Path(bundle.path))
    return UploadReviewPreview(
        upload_id=bundle.upload_id,
        queue=bundle.queue,
        action_id=action_id,
        idempotency_key=idempotency_key,
        current_state=current_state,
        proposed_state="claimed",
        branch=_deterministic_upload_branch_name(run_id, bundle.upload_id, idempotency_key),
        validation="accepted",
        reason="Deterministic upload review preview only; no queue move or curator.json write.",
        draft_status=draft.status,
        draft_paths=[file.target_path for file in draft.files],
        blocking_reason=draft.reason if draft.status == "needs_owner_action" else None,
        warnings=draft.warnings,
    )


def _state_from_queue(bundle: UploadBundleSnapshot) -> UploadLogicalState:
    if bundle.queue == "archive":
        return "archived"
    return bundle.queue


def _deterministic_upload_branch_name(run_id: str, upload_id: str, idempotency_key: str) -> str:
    short_id = idempotency_key.rsplit(":", maxsplit=1)[-1][:12]
    return f"curator/{run_id}/upload-{_slug(upload_id, fallback='upload')}-{short_id}"


def _action_for_record(
    run_id: str,
    action_index: int,
    record: FeedbackInputRecord,
    *,
    force_defer: bool = False,
) -> ProposedAction:
    evidence = ActionEvidence(
        feedback_ids=[record.feedback_id],
        upload_ids=[record.upload_id] if record.upload_id else [],
        source_ids=[record.source_id] if record.source_id else [],
        section_ids=[record.section_id] if record.section_id else [],
        result_ids=record.result_ids,
    )
    category = "upload_linked" if record.upload_id and not force_defer else record.category
    action_type, classification = _classify(
        category,
        force_defer=force_defer,
        has_corpus_target=bool(record.source_id or record.section_id or record.upload_id),
    )
    return ProposedAction(
        action_id=f"act_{action_index}",
        action_type=action_type,
        classification=classification,
        idempotency_key=deterministic_idempotency_key(action_type, evidence),
        evidence=evidence,
        target_repo=DEFAULT_TARGET_REPO if action_type in {"issue", "corpus_pr"} else None,
        validation="accepted",
        execution="not_executed",
    )


def _classify(
    category: str | None, *, force_defer: bool, has_corpus_target: bool
) -> tuple[str, str]:
    if force_defer:
        return "defer", "capacity"
    if category == "upload_linked":
        return "link_to_upload", "upload_linked"
    if category == "positive_content":
        return "no_action", "positive"
    if category in {"non_actionable", "agent_note"}:
        return "no_action", "non_actionable"
    if category == "needs_owner_action":
        return "issue", "owner_action"
    if category in {"missing_content", "wrong_content", "stale_content", "unclear_content"}:
        if not has_corpus_target:
            return "issue", "owner_action"
        return "corpus_pr", "corpus_candidate"
    return "no_action", "insufficient_evidence"


def _upload_needs_deterministic_review(bundle: UploadBundleSnapshot) -> bool:
    if bundle.manifest_error:
        return False
    if bundle.metadata_error:
        return False
    if bundle.queue in {"processed", "rejected", "archive"}:
        return False
    if bundle.curator_metadata is None:
        return bundle.queue in {"pending", "claimed", "deferred"}
    if bundle.curator_metadata.state in {"pending", "claimed"}:
        return True
    if bundle.curator_metadata.state == "deferred":
        return _upload_reentry_ready(bundle.curator_metadata)
    return False


def _upload_reentry_ready(metadata: UploadCuratorMetadata, *, now: datetime | None = None) -> bool:
    current_time = now or datetime.now(UTC)
    if metadata.reentry_trigger in IMMEDIATE_REENTRY_TRIGGERS:
        return True
    if metadata.reentry_trigger == "retry_after" and metadata.retry_after is not None:
        return metadata.retry_after <= current_time
    return False


def _slug(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48].strip("-") or fallback
