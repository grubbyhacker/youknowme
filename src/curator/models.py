from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CURATOR_SCHEMA_VERSION = "1"
CURATOR_REPORT_SCHEMA_VERSION = CURATOR_SCHEMA_VERSION
DEFAULT_STALE_LOCK_TIMEOUT_SECONDS = 7200
DEFAULT_LOCK_PATH = "/data/intake/curator-run.lock"
DEFAULT_TARGET_REPO = "grubbyhacker/ykmcorpus"
DEFAULT_PRODUCT_REPO = "grubbyhacker/youknowme"
UPLOAD_QUEUE_DIRS = ("pending", "claimed", "processed", "rejected", "archive", "deferred")

CuratorMode = Literal["dry_run", "state_only", "manual_live"]
CuratorEnabledAction = Literal["reconcile", "plan_feedback", "plan_uploads", "repair_prs"]
CuratorActionType = Literal[
    "corpus_pr",
    "corpus_issue",
    "product_issue",
    "no_action",
    "issue",
    "link_to_upload",
    "defer",
]
CuratorActionExecution = Literal["not_executed", "executed", "skipped"]
CuratorActionValidation = Literal["accepted", "rejected"]
CuratorRunStatus = Literal["pass", "fail"]
PolicyDecisionStatus = Literal["allowed", "denied"]
ExecutionOperation = Literal[
    "issue.create",
    "issue.comment",
    "issue.label.add",
    "issue.label.remove",
    "pull.create",
    "pull.review.dismiss",
    "pull.review_thread.resolve",
]
PrRepairExecutor = Literal["fixture", "codex_proxy"]
UploadReviewExecutor = Literal["codex_proxy"]
FeedbackExecutor = Literal["codex_proxy"]
PrRepairStatus = Literal[
    "validated",
    "validation_failed",
    "executor_failed",
    "push_failed",
    "pushed",
    "rejected",
]
BrokerReadOperation = Literal[
    "pull.list",
    "pull.read",
    "pull.comments",
    "pull.reviews",
    "pull.review_comments",
    "pull.review_threads",
    "commit.status",
    "check_runs",
    "issue.search",
    "issue.read",
    "issue.comments",
]
FeedbackDecisionValue = Literal[
    "no_action_positive",
    "no_action_non_actionable",
    "no_action_duplicate",
    "no_action_superseded",
    "no_action_insufficient_evidence",
    "issue_opened",
    "pr_opened",
    "linked_to_upload",
    "deferred",
    "capacity_deferred",
]
FeedbackReentryTrigger = Literal["next_run", "retry_after", "owner_input_resolved"]
UploadReentryTrigger = Literal["next_run", "retry_after", "owner_input_resolved"]
UploadLogicalState = Literal[
    "pending",
    "claimed",
    "pr_opened",
    "deferred",
    "rejected",
    "processed",
    "archived",
]
UploadDecision = Literal["integrated", "deferred", "rejected", "needs_owner_action"]
CuratorPrState = Literal[
    "open_waiting_review",
    "changes_requested",
    "commented_needs_triage",
    "checks_failed",
    "checks_missing",
    "ready_for_owner",
    "merged",
    "closed_unmerged",
    "stale_or_blocked",
]


class GithubMutationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_new_objects_per_run: int = Field(default=0, ge=0)
    upload: int = Field(default=0, ge=0)
    feedback: int = Field(default=0, ge=0)


class ModelCallBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_calls_per_run: int = Field(default=0, ge=0)
    max_tokens_per_run: int = Field(default=0, ge=0)


class CuratorTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    run_id: str
    mode: CuratorMode = "dry_run"
    enabled_actions: list[CuratorEnabledAction] = Field(default_factory=list)
    upload_ids: list[str] = Field(default_factory=list)
    github_mutation_budget: GithubMutationBudget = Field(default_factory=GithubMutationBudget)
    model_call_budget: ModelCallBudget = Field(default_factory=ModelCallBudget)
    model_feedback_planning: bool = False
    feedback_model: str | None = None
    feedback_executor: FeedbackExecutor | None = None
    feedback_agent_model: str = "ykm-codex-gpt-5-mini"
    feedback_agent_max_attempts: int | None = Field(default=None, ge=1)
    feedback_agent_validation_command: list[str] = Field(
        default_factory=lambda: ["mise", "run", "validate"],
        min_length=1,
    )
    model_upload_review: bool = False
    upload_review_model: str | None = None
    upload_review_executor: UploadReviewExecutor | None = None
    upload_review_agent_model: str = "ykm-codex-gpt-5-mini"
    upload_review_max_attempts: int | None = Field(default=None, ge=1)
    upload_review_validation_command: list[str] = Field(
        default_factory=lambda: ["mise", "run", "validate"],
        min_length=1,
    )
    pr_repair_executor: PrRepairExecutor | None = None
    pr_repair_model: str = "ykm-codex-gpt-5-mini"
    pr_repair_max_per_run: int = Field(default=1, ge=0)
    pr_repair_validation_command: list[str] = Field(
        default_factory=lambda: ["mise", "run", "validate"],
        min_length=1,
    )
    feedback_soft_action_threshold: int = Field(default=10, ge=0)
    stale_lock_timeout_seconds: int = Field(default=DEFAULT_STALE_LOCK_TIMEOUT_SECONDS, ge=1)


class CuratorRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str
    intake: Path
    output: Path
    logs: Path | None = None
    task: Path | None = None
    broker_url: str | None = None
    model_proxy_url: str | None = None
    model_proxy_token: str | None = None
    broker_fixture: Path | None = None
    model_proxy_fixture: Path | None = None
    required_broker: bool = False
    required_model_proxy: bool = False
    lock_path: Path | None = None
    recover_stale_lock: bool = False
    simulate_execution: bool = False
    enable_broker_reads: bool = False
    corpus_checkout: Path | None = None
    codex_proxy_base_url: str | None = None
    codex_proxy_token: str | None = None


class FeedbackCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "feedback/feedback.jsonl"
    byte_offset: int = Field(default=0, ge=0)


class CuratorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    last_completed_run_id: str | None = None
    feedback_checkpoint: FeedbackCheckpoint = Field(default_factory=FeedbackCheckpoint)
    updated_at: datetime | None = None


class FeedbackWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)


class ActionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_ids: list[str] = Field(default_factory=list)
    upload_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)


class FeedbackInputRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    feedback_id: str
    category: str | None = None
    source_id: str | None = None
    section_id: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    upload_id: str | None = None


class InputRecordError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line_number: int | None = None
    byte_offset: int | None = Field(default=None, ge=0)
    category: Literal["invalid_json", "invalid_schema", "invalid_type", "read_error"]
    message: str


class FeedbackWindowReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[InputRecordError] = Field(default_factory=list)


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    action_type: CuratorActionType
    classification: str
    idempotency_key: str
    evidence: ActionEvidence = Field(default_factory=ActionEvidence)
    target_repo: str | None = None
    validation: CuratorActionValidation = "accepted"
    execution: CuratorActionExecution = "not_executed"

    @model_validator(mode="after")
    def _validate_evidence_and_key(self) -> ProposedAction:
        if not (
            self.evidence.feedback_ids
            or self.evidence.upload_ids
            or self.evidence.source_ids
            or self.evidence.section_ids
            or self.evidence.result_ids
        ):
            raise ValueError("proposed actions must cite durable evidence identifiers")
        if not self.idempotency_key.startswith(f"{self.action_type}:"):
            raise ValueError("idempotency key must be prefixed by action type")
        return self


class FeedbackPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    run_id: str
    feedback_window: FeedbackWindow
    included_feedback_ids: list[str] = Field(default_factory=list)
    reentered_feedback_ids: list[str] = Field(default_factory=list)
    referenced_upload_ids: list[str] = Field(default_factory=list)
    referenced_source_ids: list[str] = Field(default_factory=list)
    referenced_section_ids: list[str] = Field(default_factory=list)
    referenced_result_ids: list[str] = Field(default_factory=list)
    soft_action_threshold: int = Field(default=10, ge=0)
    capacity_deferred_feedback_ids: list[str] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    created_at: datetime


class UploadReviewPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    queue: Literal["pending", "claimed", "processed", "rejected", "archive", "deferred"]
    action_id: str
    idempotency_key: str
    current_state: UploadLogicalState
    proposed_state: UploadLogicalState
    branch: str
    validation: CuratorActionValidation = "accepted"
    reason: str
    draft_status: Literal[
        "not_evaluated",
        "corpus_pr_candidate",
        "model_review_candidate",
        "needs_owner_action",
    ] = "not_evaluated"
    draft_paths: list[str] = Field(default_factory=list)
    blocking_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class UploadPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    run_id: str
    included_upload_ids: list[str] = Field(default_factory=list)
    review_previews: list[UploadReviewPreview] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    created_at: datetime


class FeedbackDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    feedback_id: str
    run_id: str
    plan_action_id: str
    decision: FeedbackDecisionValue
    pr_number: int | None = None
    issue_number: int | None = None
    source_id: str | None = None
    section_id: str | None = None
    upload_id: str | None = None
    reentry_trigger: FeedbackReentryTrigger | None = None
    retry_after: datetime | None = None
    reason: str
    timestamp: datetime

    @model_validator(mode="after")
    def _validate_reentry_trigger(self) -> FeedbackDecision:
        if self.reentry_trigger == "retry_after" and self.retry_after is None:
            raise ValueError("retry_after reentry trigger requires retry_after timestamp")
        return self


class UploadCuratorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    upload_id: str
    state: UploadLogicalState
    decision: UploadDecision | None = None
    run_id: str
    branch: str | None = None
    pr_number: int | None = None
    issue_number: int | None = None
    blocking_issue_number: int | None = None
    claimed_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_action_at: datetime | None = None
    reentry_trigger: UploadReentryTrigger | None = None
    retry_after: datetime | None = None
    blocking_reason: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_reentry_trigger(self) -> UploadCuratorMetadata:
        if self.reentry_trigger == "retry_after" and self.retry_after is None:
            raise ValueError("retry_after reentry trigger requires retry_after timestamp")
        return self


class UploadBundleSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    queue: Literal["pending", "claimed", "processed", "rejected", "archive", "deferred"]
    path: str
    has_manifest: bool
    manifest_upload_id: str | None = None
    manifest_error: str | None = None
    has_curator_metadata: bool
    curator_metadata: UploadCuratorMetadata | None = None
    metadata_error: str | None = None


class UploadQueueSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counts: dict[str, int]
    pending_uploads: list[str] = Field(default_factory=list)
    bundles: list[UploadBundleSnapshot] = Field(default_factory=list)


class BranchPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    idempotency_key: str
    branch: str


class BranchCollision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    branch: str
    existing_upload_id: str


class CuratorPrReviewSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    database_id: int | None = None
    state: str
    author_login: str | None = None
    body: str = ""
    submitted_at: str | None = None


class CuratorPrReviewCommentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    database_id: int | None = None
    author_login: str | None = None
    body: str = ""
    path: str | None = None
    line: int | None = None


class CuratorPrReviewThreadSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    database_id: int | None = None
    is_resolved: bool = False
    path: str | None = None
    line: int | None = None
    comments: list[CuratorPrReviewCommentSnapshot] = Field(default_factory=list)


class CuratorPrSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    state: Literal["open", "closed", "merged"]
    title: str | None = None
    body: str = ""
    branch: str | None = None
    labels: list[str] = Field(default_factory=list)
    review_comments: list[str] = Field(default_factory=list)
    reviews: list[CuratorPrReviewSnapshot] = Field(default_factory=list)
    review_threads: list[CuratorPrReviewThreadSnapshot] = Field(default_factory=list)
    checks_conclusion: Literal["success", "failure", "pending", "missing", "unknown"] = "unknown"
    unresolved_thread_count: int = Field(default=0, ge=0)
    review_decision: Literal["approved", "changes_requested", "commented", "none"] = "none"


class CuratorIssueSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    state: Literal["open", "closed"]
    title: str | None = None
    body: str = ""


class CuratorPrReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_number: int
    pr_state: CuratorPrState
    branch: str | None = None
    labels: list[str] = Field(default_factory=list)
    run_id: str | None = None
    action_id: str | None = None
    idempotency_key: str | None = None
    upload_ids: list[str] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    reason: str


class UploadTransitionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    pr_number: int | None = None
    issue_number: int | None = None
    from_state: UploadLogicalState
    to_state: UploadLogicalState
    validation: CuratorActionValidation
    reason: str


class FeedbackDecisionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    pr_number: int | None = None
    issue_number: int | None = None
    from_decision: FeedbackDecisionValue | None = None
    to_decision: FeedbackDecisionValue
    validation: CuratorActionValidation
    reason: str


class ReconciliationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_window_record_count: int = 0
    decided_feedback_count: int = 0
    undecided_feedback_count: int = 0
    reentered_feedback_count: int = 0
    upload_metadata_state_counts: dict[str, int] = Field(default_factory=dict)
    invalid_upload_metadata_count: int = 0
    branch_previews: list[BranchPreview] = Field(default_factory=list)
    branch_collision_count: int = 0
    branch_collisions: list[BranchCollision] = Field(default_factory=list)
    pr_reconciliation_count: int = 0
    pr_state_counts: dict[str, int] = Field(default_factory=dict)
    pr_reconciliations: list[CuratorPrReconciliation] = Field(default_factory=list)
    upload_transition_preview_count: int = 0
    upload_transition_previews: list[UploadTransitionPreview] = Field(default_factory=list)
    feedback_decision_preview_count: int = 0
    feedback_decision_previews: list[FeedbackDecisionPreview] = Field(default_factory=list)
    feedback_reentry_preview_count: int = 0
    feedback_reentry_previews: list[FeedbackDecisionPreview] = Field(default_factory=list)


class CuratorExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_issue_repos: list[str] = Field(
        default_factory=lambda: ["grubbyhacker/ykmcorpus", "grubbyhacker/youknowme"]
    )
    allowed_pr_repos: list[str] = Field(default_factory=lambda: ["grubbyhacker/ykmcorpus"])
    max_new_objects_per_run: int = Field(default=0, ge=0)
    upload_new_object_budget: int = Field(default=0, ge=0)
    feedback_new_object_budget: int = Field(default=0, ge=0)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    action_type: CuratorActionType
    idempotency_key: str
    status: PolicyDecisionStatus
    reason: str
    target_repo: str | None = None
    budget_bucket: Literal["feedback", "upload", "none"] = "none"


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    operation: ExecutionOperation
    idempotency_key: str
    target_repo: str
    branch: str | None = None
    evidence: ActionEvidence
    title: str | None = None
    body: str | None = None
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    execution: Literal["not_executed"] = "not_executed"


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    operation: ExecutionOperation
    idempotency_key: str
    status: Literal["simulated", "executed", "failed"]
    target_repo: str
    branch: str | None = None
    pr_number: int | None = None
    issue_number: int | None = None
    url: str | None = None
    message: str | None = None


class PrRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_number: int
    branch: str | None = None
    pr_state: CuratorPrState
    executor: PrRepairExecutor
    model: str | None = None
    status: PrRepairStatus
    message: str
    changed_files: list[str] = Field(default_factory=list)
    repair_head_sha: str | None = None
    diff_stat: str | None = None
    validation_command: list[str] = Field(default_factory=list)
    validation_returncode: int | None = None
    validation_stdout_tail: str = ""
    validation_stderr_tail: str = ""
    transcript_path: str | None = None
    review_request_comment: str | None = None
    review_request_comment_status: Literal["not_applicable", "pending", "posted", "failed"] = (
        "not_applicable"
    )
    review_request_comment_message: str | None = None
    dismissed_review_count: int = 0
    resolved_thread_count: int = 0
    label_update_count: int = 0
    pushed: bool = False


class BrokerReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: BrokerReadOperation
    method: Literal["GET"] = "GET"
    path: str
    target_repo: str
    idempotency_key: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    purpose: str


class BrokerFixtureState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    reachable: bool = True
    existing_branches: list[str] = Field(default_factory=list)
    existing_idempotency_keys: list[str] = Field(default_factory=list)
    allowed_operations: list[ExecutionOperation] = Field(
        default_factory=lambda: [
            "issue.create",
            "issue.comment",
            "issue.label.add",
            "issue.label.remove",
            "pull.create",
            "pull.review.dismiss",
            "pull.review_thread.resolve",
        ]
    )
    pr_snapshots: list[CuratorPrSnapshot] = Field(default_factory=list)
    issue_snapshots: list[CuratorIssueSnapshot] = Field(default_factory=list)


class ModelCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    task_name: str
    run_id: str | None = None
    model: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    max_tokens: int | None = Field(default=None, ge=1)


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ModelCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    task_name: str
    output: dict[str, Any] = Field(default_factory=dict)
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ModelProxyFixtureState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_SCHEMA_VERSION
    reachable: bool = True
    max_calls_per_run: int = Field(default=0, ge=0)
    max_tokens_per_run: int = Field(default=0, ge=0)
    responses: dict[str, ModelCallResponse] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_response_keys(self) -> ModelProxyFixtureState:
        for task_name, response in self.responses.items():
            if response.task_name != task_name:
                raise ValueError(
                    f"model fixture response key {task_name!r} does not match task_name "
                    f"{response.task_name!r}"
                )
        return self


class CuratorProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["pass", "fail", "skip"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CuratorRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = CURATOR_REPORT_SCHEMA_VERSION
    run_id: str
    mode: CuratorMode = "dry_run"
    created_at: datetime
    started_at: datetime
    completed_at: datetime
    status: CuratorRunStatus
    task: dict[str, Any] | None = None
    enabled_actions: list[CuratorEnabledAction] = Field(default_factory=list)
    intake_path: str
    logs_path: str | None = None
    output_path: str
    lock_path: str
    feedback_window: dict[str, int]
    feedback_checkpoint: dict[str, Any]
    checkpoint_advanced: bool
    feedback_plan_paths: list[str] = Field(default_factory=list)
    included_feedback_ids: list[str]
    feedback_decision_count: int
    feedback_decisions_appended: int = 0
    upload_plan_paths: list[str] = Field(default_factory=list)
    included_upload_ids: list[str] = Field(default_factory=list)
    upload_queue_counts: dict[str, int]
    pending_uploads: list[str]
    upload_bundles: list[dict[str, Any]] = Field(default_factory=list)
    proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    proposed_action_count: int
    upload_proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    upload_proposed_action_count: int = 0
    upload_review_previews: list[dict[str, Any]] = Field(default_factory=list)
    upload_review_preview_count: int = 0
    upload_review_observations: list[dict[str, Any]] = Field(default_factory=list)
    upload_review_observation_count: int = 0
    upload_review_validation_failure_count: int = 0
    pr_repair_results: list[dict[str, Any]] = Field(default_factory=list)
    pr_repair_result_count: int = 0
    pr_repair_validation_failure_count: int = 0
    upload_metadata_update_count: int = 0
    upload_metadata_update_paths: list[str] = Field(default_factory=list)
    referenced_upload_ids: list[str] = Field(default_factory=list)
    referenced_source_ids: list[str] = Field(default_factory=list)
    referenced_section_ids: list[str] = Field(default_factory=list)
    referenced_result_ids: list[str] = Field(default_factory=list)
    executed_action_count: int = 0
    github_mutation_count: int = 0
    github_mutation_budget: dict[str, int] = Field(default_factory=dict)
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list)
    policy_denial_count: int = 0
    execution_intents: list[dict[str, Any]] = Field(default_factory=list)
    execution_intent_count: int = 0
    simulated_execution_results: list[dict[str, Any]] = Field(default_factory=list)
    simulated_execution_count: int = 0
    capacity_deferral_count: int = 0
    capacity_deferred_feedback_ids: list[str] = Field(default_factory=list)
    validation_failure_count: int = 0
    input_error_count: int = 0
    input_errors: list[dict[str, Any]] = Field(default_factory=list)
    model_call_count: int = 0
    model_call_budget: dict[str, int] = Field(default_factory=dict)
    model_token_count: int = 0
    model_budget_exhausted: bool = False
    feedback_count: int
    query_log_count: int
    reconciliation: dict[str, Any] = Field(default_factory=dict)
    partial_failures: list[dict[str, Any]] = Field(default_factory=list)
    probes: list[CuratorProbe]
    proposed_state: dict[str, Any] | None = None
