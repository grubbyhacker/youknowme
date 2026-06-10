from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from curator.adapters import (
    FixtureBrokerAdapter,
    FixtureModelAdapter,
    HttpBrokerAdapter,
    HttpModelProxyAdapter,
)
from curator.models import (
    DEFAULT_LOCK_PATH,
    DEFAULT_STALE_LOCK_TIMEOUT_SECONDS,
    CuratorIssueSnapshot,
    CuratorProbe,
    CuratorPrSnapshot,
    CuratorRunConfig,
    CuratorRunReport,
    CuratorState,
    CuratorTask,
    ExecutionIntent,
    ExecutionResult,
    FeedbackInputRecord,
    FeedbackDecision,
    FeedbackPlan,
    ModelCallBudget,
    ModelCallRequest,
    PrRepairResult,
    UploadPlan,
    UploadDecision,
    UploadQueueSnapshot,
    UploadTransitionPreview,
)
from curator.model_tasks import (
    FeedbackPlanningModelOutput,
    UploadReviewModelOutput,
    build_feedback_planning_proposed_actions,
    strict_model_json_schema,
    validate_feedback_planning_model_output,
    validate_model_response_output,
)
from curator.execution import (
    append_feedback_decisions,
    build_execution_intents,
    reconciliation_feedback_decisions,
    reconciliation_feedback_reentry_decisions,
    state_only_feedback_decisions,
)
from curator.planning import build_feedback_plan, build_upload_plan, ready_reentry_feedback_ids
from curator.policy import evaluate_feedback_action_policy, policy_from_budget
from curator.pr_repair import execute_pr_repairs
from curator.pr_reconcile import CURATOR_NEEDS_WORK_LABEL, CURATOR_WAITING_REVIEW_LABEL
from curator.reconcile import build_reconciliation_summary
from curator.state import (
    CuratorLiveLockError,
    CuratorRunLock,
    CuratorStaleLockError,
    advanced_state,
    freeze_feedback_window,
    load_latest_feedback_decisions,
    read_curator_state,
    read_feedback_records_by_id,
    read_feedback_window_result,
    snapshot_upload_queue,
    write_curator_state,
)
from curator.upload_draft import ALLOWED_TAGS, ALLOWED_TYPES
from curator.upload_observe import UploadReviewObservation, observe_upload_review_draft
from curator.upload_pr import execute_upload_review_pr, upload_review_pull_intent
from curator.upload_state import transition_upload_metadata


DEFAULT_FORBIDDEN_ENV = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "YKM_GITHUB_PRIVATE_KEY_PATH",
    "YKM_CF_ACCESS_CLIENT_SECRET",
)
DEFAULT_CORPUS_REPO = "grubbyhacker/ykmcorpus"
MAX_UPLOAD_REVIEW_FILES = 5
MAX_UPLOAD_REVIEW_FILE_CHARS = 20000
MAX_UPLOAD_REVIEW_MANIFEST_CHARS = 20000


CuratorDryRunConfig = CuratorRunConfig
CuratorDryRunReport = CuratorRunReport


def run_curator_dry_run(config: CuratorDryRunConfig) -> CuratorDryRunReport:
    probes: list[CuratorProbe] = []
    started_at = datetime.now(UTC)
    task_payload, task_model = _read_task(config.task, probes)
    run_id = task_model.run_id if task_model is not None else config.run_id
    mode = task_model.mode if task_model is not None else "dry_run"
    enabled_actions = set(task_model.enabled_actions if task_model is not None else [])
    if not enabled_actions:
        enabled_actions = {"reconcile", "plan_feedback", "plan_uploads"}
    feedback_soft_action_threshold = (
        task_model.feedback_soft_action_threshold if task_model is not None else 10
    )
    model_feedback_planning = (
        task_model.model_feedback_planning if task_model is not None else False
    )
    feedback_model = task_model.feedback_model if task_model is not None else None
    model_upload_review = task_model.model_upload_review if task_model is not None else False
    upload_review_model = task_model.upload_review_model if task_model is not None else None
    pr_repair_executor = task_model.pr_repair_executor if task_model is not None else None
    pr_repair_model = (
        task_model.pr_repair_model if task_model is not None else "ykm-codex-gpt-5-mini"
    )
    pr_repair_max_per_run = task_model.pr_repair_max_per_run if task_model is not None else 1
    pr_repair_validation_command = (
        task_model.pr_repair_validation_command
        if task_model is not None
        else ["mise", "run", "validate"]
    )
    github_mutation_budget = (
        task_model.github_mutation_budget.model_dump() if task_model is not None else {}
    )
    model_call_budget = task_model.model_call_budget.model_dump() if task_model is not None else {}
    stale_timeout = (
        task_model.stale_lock_timeout_seconds
        if task_model is not None
        else DEFAULT_STALE_LOCK_TIMEOUT_SECONDS
    )
    lock_path = config.lock_path or (config.intake / "curator-run.lock")
    if str(lock_path) == ".":
        lock_path = Path(DEFAULT_LOCK_PATH)

    report_context = {
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at,
        "task_payload": task_payload,
        "lock_path": lock_path,
        "enabled_actions": enabled_actions,
        "feedback_soft_action_threshold": feedback_soft_action_threshold,
        "github_mutation_budget": github_mutation_budget,
        "model_call_budget": model_call_budget,
        "model_feedback_planning": model_feedback_planning,
        "feedback_model": feedback_model,
        "model_upload_review": model_upload_review,
        "upload_review_model": upload_review_model,
        "pr_repair_executor": pr_repair_executor,
        "pr_repair_model": pr_repair_model,
        "pr_repair_max_per_run": pr_repair_max_per_run,
        "pr_repair_validation_command": pr_repair_validation_command,
    }
    _check_forbidden_env(probes)
    if any(probe.name == "task" and probe.status == "fail" for probe in probes):
        report = _empty_report(config, probes, status="fail", **report_context)
        write_curator_reports(report, config.output)
        return report
    if any(probe.name == "forbidden-env" and probe.status == "fail" for probe in probes):
        report = _empty_report(config, probes, status="fail", **report_context)
        write_curator_reports(report, config.output)
        return report
    try:
        with CuratorRunLock(
            lock_path,
            run_id=run_id,
            stale_timeout_seconds=stale_timeout,
            recover_stale=config.recover_stale_lock,
        ):
            probes.append(CuratorProbe(name="lock", status="pass", message="curator lock acquired"))
            return _run_with_lock(config, probes, **report_context)
    except (CuratorLiveLockError, CuratorStaleLockError) as exc:
        probes.append(CuratorProbe(name="lock", status="fail", message=str(exc)))
        report = _empty_report(config, probes, status="fail", **report_context)
        write_curator_reports(report, config.output)
        return report


def _run_with_lock(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
    *,
    run_id: str,
    mode: str,
    started_at: datetime,
    task_payload: dict[str, Any] | None,
    lock_path: Path,
    enabled_actions: set[str],
    feedback_soft_action_threshold: int,
    github_mutation_budget: dict[str, int],
    model_call_budget: dict[str, int],
    model_feedback_planning: bool,
    feedback_model: str | None,
    model_upload_review: bool,
    upload_review_model: str | None,
    pr_repair_executor: str | None,
    pr_repair_model: str,
    pr_repair_max_per_run: int,
    pr_repair_validation_command: list[str],
) -> CuratorDryRunReport:
    _check_directory(config.intake, "intake", probes, writable=False)
    _check_directory(config.output, "output", probes, writable=True)
    if config.logs is not None and config.logs.exists():
        _check_directory(config.logs, "logs", probes, writable=False)
    elif config.logs is not None:
        probes.append(
            CuratorProbe(name="logs", status="skip", message=f"logs directory absent: {config.logs}")
        )

    queue_snapshot = snapshot_upload_queue(config.intake)
    probes.append(
        CuratorProbe(
            name="uploads",
            status="pass",
            message="upload queue snapshot captured",
            details={"counts": queue_snapshot.counts},
        )
    )

    state_path = config.intake / "feedback" / "curator-state.json"
    feedback_path = config.intake / "feedback" / "feedback.jsonl"
    decisions_path = config.intake / "feedback" / "curator-decisions.jsonl"
    input_errors = []
    try:
        state = read_curator_state(state_path)
        feedback_window = freeze_feedback_window(feedback_path, state)
        feedback_read = read_feedback_window_result(feedback_path, feedback_window)
        feedback_records = feedback_read.records
        input_errors = [error.model_dump(mode="json") for error in feedback_read.errors]
        latest_decisions = load_latest_feedback_decisions(decisions_path)
        reentry_read = read_feedback_records_by_id(
            feedback_path,
            _historical_reentry_ids(latest_decisions, feedback_records),
        )
        feedback_records = _merge_feedback_records(feedback_records, reentry_read.records)
        input_errors.extend(error.model_dump(mode="json") for error in reentry_read.errors)
        probes.append(
            CuratorProbe(
                name="feedback-window",
                status="pass",
                message="feedback window frozen",
                details=feedback_window.model_dump(),
            )
        )
        if input_errors:
            probes.append(
                CuratorProbe(
                    name="feedback-input",
                    status="fail",
                    message=f"{len(input_errors)} feedback input records are invalid",
                    details={"errors": input_errors},
                )
            )
        if reentry_read.records:
            probes.append(
                CuratorProbe(
                    name="feedback-reentry",
                    status="pass",
                    message="ready deferred feedback re-entered from historical feedback log",
                    details={
                        "feedback_ids": [
                            record["feedback_id"]
                            for record in reentry_read.records
                            if isinstance(record.get("feedback_id"), str)
                        ]
                    },
                )
            )
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        state = CuratorState()
        feedback_window = freeze_feedback_window(Path("/path/that/does/not/exist"), state)
        feedback_records = []
        latest_decisions = {}
        probes.append(CuratorProbe(name="feedback-window", status="fail", message=str(exc)))

    feedback_plan = build_feedback_plan(
        run_id=run_id,
        feedback_window=feedback_window,
        feedback_records=feedback_records if "plan_feedback" in enabled_actions else [],
        latest_decisions=latest_decisions,
        soft_action_threshold=feedback_soft_action_threshold,
    )
    model_call_count = 0
    model_token_count = 0
    if "plan_feedback" in enabled_actions and model_feedback_planning:
        feedback_plan, model_probe, model_usage = _apply_model_feedback_planning(
            config=config,
            run_id=run_id,
            model=feedback_model,
            model_call_budget=ModelCallBudget.model_validate(model_call_budget),
            base_plan=feedback_plan,
            feedback_records=feedback_records,
        )
        probes.append(model_probe)
        model_call_count += model_usage["call_count"]
        model_token_count += model_usage["token_count"]
    if "plan_uploads" in enabled_actions:
        upload_plan = build_upload_plan(run_id=run_id, upload_snapshot=queue_snapshot)
    else:
        upload_plan = UploadPlan(run_id=run_id, created_at=datetime.now(UTC))
    upload_review_observations: list[UploadReviewObservation] = []
    if "plan_uploads" in enabled_actions and model_upload_review:
        (
            observations,
            upload_model_outputs,
            upload_model_probe,
            upload_model_usage,
        ) = _apply_model_upload_review(
            config=config,
            upload_plan=upload_plan,
            upload_snapshot=queue_snapshot,
            model=upload_review_model,
            model_call_budget=ModelCallBudget.model_validate(model_call_budget),
            used_model_calls=model_call_count,
        )
        upload_review_observations = observations
        probes.append(upload_model_probe)
        model_call_count += upload_model_usage["call_count"]
        model_token_count += upload_model_usage["token_count"]
    else:
        upload_model_outputs = {}
    pr_snapshots: list[CuratorPrSnapshot] = []
    issue_snapshots: list[CuratorIssueSnapshot] = []
    if "reconcile" in enabled_actions:
        pr_snapshots = _load_broker_fixture_pr_snapshots(config, probes)
        issue_snapshots = _load_broker_fixture_issue_snapshots(config, probes)
        if config.enable_broker_reads and config.broker_fixture is None:
            pr_snapshots = _load_http_broker_pr_snapshots(config, probes)
            issue_snapshots = _load_http_broker_issue_snapshots(
                config,
                probes,
                issue_numbers=_known_issue_numbers(latest_decisions, queue_snapshot),
            )
    reconciliation = build_reconciliation_summary(
        feedback_records=feedback_records,
        latest_decisions=latest_decisions,
        feedback_plan=feedback_plan,
        upload_snapshot=queue_snapshot,
        upload_plan=upload_plan,
        pr_snapshots=pr_snapshots,
        issue_snapshots=issue_snapshots,
    )
    pr_repair_results = []
    pr_repair_handoff_results: list[ExecutionResult] = []
    if "repair_prs" in enabled_actions:
        if pr_repair_executor is None:
            probes.append(
                CuratorProbe(
                    name="pr-repair",
                    status="fail",
                    message="repair_prs requires pr_repair_executor in the task contract",
                )
            )
        elif "reconcile" not in enabled_actions:
            probes.append(
                CuratorProbe(
                    name="pr-repair",
                    status="fail",
                    message="repair_prs requires reconcile so actionable PRs can be selected",
                )
            )
        else:
            pr_repair_results = execute_pr_repairs(
                run_id=run_id,
                mode=mode,
                reconciliations=reconciliation.pr_reconciliations,
                snapshots=pr_snapshots,
                executor=pr_repair_executor,
                model=pr_repair_model,
                validation_command=pr_repair_validation_command,
                max_repairs=pr_repair_max_per_run,
                output=config.output,
                broker_remote_url=_broker_remote_url(config, task_payload)
                if (config.broker_url or _task_broker_remote_url(task_payload))
                else None,
                codex_proxy_base_url=(
                    config.codex_proxy_base_url
                    or config.model_proxy_url
                    or "http://gh-agent-proxy:8092"
                ),
                codex_proxy_token=config.codex_proxy_token or config.model_proxy_token,
            )
            failed_repairs = [
                result
                for result in pr_repair_results
                if result.status
                in {"validation_failed", "executor_failed", "push_failed", "rejected"}
            ]
            probes.append(
                CuratorProbe(
                    name="pr-repair",
                    status="fail" if failed_repairs else "pass",
                    message="PR repair execution completed",
                    details={
                        "result_count": len(pr_repair_results),
                        "failed_count": len(failed_repairs),
                    },
                )
            )
            if mode == "manual_live":
                pr_repair_handoff_results = _complete_pr_repair_handoffs(
                    config=config,
                    results=pr_repair_results,
                    snapshots=pr_snapshots,
                )
                if pr_repair_handoff_results:
                    failed_comments = [
                        result
                        for result in pr_repair_handoff_results
                        if result.status == "failed"
                    ]
                    probes.append(
                        CuratorProbe(
                            name="pr-repair-handoff",
                            status="fail" if failed_comments else "pass",
                            message="PR repair handoff mutations processed",
                            details={
                                "result_count": len(pr_repair_handoff_results),
                                "failed_count": len(failed_comments),
                            },
                        )
                    )
    policy_decisions = evaluate_feedback_action_policy(
        feedback_plan.proposed_actions,
        policy_from_budget(github_mutation_budget),
    )
    execution_intents = build_execution_intents(
        run_id, feedback_plan.proposed_actions, policy_decisions
    )
    execution_intents.extend(
        _upload_review_execution_intents(
            run_id=run_id,
            upload_plan=upload_plan,
            observations=upload_review_observations,
        )
    )
    metadata_error_count = sum(1 for bundle in queue_snapshot.bundles if bundle.metadata_error)
    manifest_error_count = sum(1 for bundle in queue_snapshot.bundles if bundle.manifest_error)
    input_error_count = len(input_errors)
    branch_collision_count = reconciliation.branch_collision_count
    state_blocking_validation_failure_count = (
        metadata_error_count + manifest_error_count + input_error_count + branch_collision_count
    )
    if metadata_error_count:
        probes.append(
            CuratorProbe(
                name="upload-metadata",
                status="fail",
                message=f"{metadata_error_count} upload curator metadata records are invalid",
            )
        )
    if manifest_error_count:
        probes.append(
            CuratorProbe(
                name="upload-manifest",
                status="fail",
                message=f"{manifest_error_count} upload manifests are invalid",
            )
        )
    if branch_collision_count:
        probes.append(
            CuratorProbe(
                name="branch-preflight",
                status="fail",
                message=f"{branch_collision_count} proposed Curator branch names collide with existing metadata",
            )
        )
    simulated_execution_results = []
    live_execution_results = []
    policy_denial_count = sum(1 for decision in policy_decisions if decision.status == "denied")
    if policy_denial_count:
        probes.append(
            CuratorProbe(
                name="execution-policy",
                status="fail" if mode == "manual_live" else "skip",
                message=f"{policy_denial_count} proposed actions are denied by deterministic execution policy",
            )
        )

    requires_broker = config.required_broker or (
        mode == "manual_live" and _requires_broker(feedback_plan, upload_plan)
    ) or config.enable_broker_reads or (
        "repair_prs" in enabled_actions and pr_repair_executor == "codex_proxy"
    )
    _probe_broker(config, probes, required=requires_broker)
    if config.broker_fixture is not None:
        try:
            fixture_broker = FixtureBrokerAdapter.from_path(config.broker_fixture)
            probes.extend(fixture_broker.preflight_intents(execution_intents))
            if "plan_uploads" in enabled_actions:
                probes.extend(
                    fixture_broker.preflight_upload_review_previews(upload_plan.review_previews)
                )
            if config.simulate_execution and not any(
                probe.name == "broker-preflight" and probe.status == "fail" for probe in probes
            ):
                simulated_execution_results = fixture_broker.simulate_intents(execution_intents)
        except (OSError, ValidationError) as exc:
            probes.append(
                CuratorProbe(
                    name="broker-preflight",
                    status="fail",
                    message=f"broker fixture preflight failed: {exc}",
                )
            )
    elif config.simulate_execution:
        probes.append(
            CuratorProbe(
                name="fixture-execution",
                status="fail",
                message="fixture execution simulation requires --broker-fixture",
            )
        )
    elif config.broker_url is not None and execution_intents:
        probes.extend(HttpBrokerAdapter(config.broker_url).preflight_intents(execution_intents))
    if config.broker_url is not None and "plan_uploads" in enabled_actions:
        upload_preflight = HttpBrokerAdapter(config.broker_url).upload_review_preflight(
            target_repo=DEFAULT_CORPUS_REPO,
            previews=upload_plan.review_previews,
        )
        if upload_preflight is not None:
            probes.append(upload_preflight)
    if config.broker_url is not None and "reconcile" in enabled_actions:
        probes.append(
            HttpBrokerAdapter(config.broker_url).pr_reconciliation_preflight(
                target_repo=DEFAULT_CORPUS_REPO,
                snapshots=pr_snapshots,
            )
        )
        issue_preflight = HttpBrokerAdapter(config.broker_url).issue_reconciliation_preflight(
            target_repo=DEFAULT_CORPUS_REPO,
            issue_numbers=_known_issue_numbers(latest_decisions, queue_snapshot),
        )
        if issue_preflight is not None:
            probes.append(issue_preflight)
    requires_model_proxy = config.required_model_proxy or (
        mode == "manual_live" and model_call_budget.get("max_calls_per_run", 0) > 0
    ) or model_feedback_planning or model_upload_review or (
        "repair_prs" in enabled_actions and pr_repair_executor == "codex_proxy"
    )
    _probe_model_proxy(config, probes, required=requires_model_proxy)
    _probe_model_budget(config, probes, model_call_budget)
    model_budget_exhausted = any(
        probe.name == "model-budget" and probe.status == "fail" for probe in probes
    )
    state_preflight_failure_count = _state_only_preflight_failure_count(probes)
    upload_review_validation_failure_count = sum(
        1 for observation in upload_review_observations if observation.status == "fail"
    )

    proposed_state = advanced_state(run_id, state, feedback_window)
    checkpoint_advanced = False
    feedback_plan_paths: list[str] = []
    upload_plan_paths: list[str] = []
    decisions_appended = 0
    upload_metadata_update_paths: list[str] = []
    if mode == "state_only":
        _record_feedback_plan_write(
            config.intake / "feedback" / "runs" / run_id,
            feedback_plan,
            feedback_plan_paths,
            probes,
        )
        _record_feedback_plan_write(
            config.output / "feedback" / "runs" / run_id,
            feedback_plan,
            feedback_plan_paths,
            probes,
        )
        _record_upload_plan_write(
            config.intake / "uploads" / "runs" / run_id,
            upload_plan,
            upload_plan_paths,
            probes,
        )
        _record_upload_plan_write(
            config.output / "uploads" / "runs" / run_id,
            upload_plan,
            upload_plan_paths,
            probes,
        )
        if state_blocking_validation_failure_count or state_preflight_failure_count:
            probes.append(
                CuratorProbe(
                    name="state-only",
                    status="fail",
                    message="state_only writes skipped because validation or preflight failures are present",
                    details={
                        "validation_failure_count": state_blocking_validation_failure_count,
                        "preflight_failure_count": state_preflight_failure_count,
                    },
                )
            )
        else:
            state_only_decisions = state_only_feedback_decisions(run_id, feedback_plan)
            if "reconcile" in enabled_actions:
                state_only_decisions.extend(
                    reconciliation_feedback_decisions(
                        run_id,
                        reconciliation.feedback_decision_previews,
                    )
                )
                state_only_decisions.extend(
                    reconciliation_feedback_reentry_decisions(
                        run_id,
                        reconciliation.feedback_reentry_previews,
                    )
                )
            decisions_appended, decision_append_probe = _append_feedback_decisions_safely(
                decisions_path,
                state_only_decisions,
            )
            if decision_append_probe is not None:
                probes.append(decision_append_probe)
            unresolved_feedback_ids = _state_only_unresolved_feedback_ids(
                feedback_plan,
                state_only_decisions,
            )
            if unresolved_feedback_ids:
                probes.append(
                    CuratorProbe(
                        name="state-only",
                        status="fail",
                        message="state_only checkpoint not advanced because feedback remains without a state-only decision",
                        details={"feedback_ids": unresolved_feedback_ids},
                    )
                )
            if "reconcile" in enabled_actions:
                upload_metadata_update_paths, upload_metadata_update_probes = (
                    _apply_upload_transition_previews(
                        run_id=run_id,
                        upload_snapshot=queue_snapshot,
                        previews=reconciliation.upload_transition_previews,
                    )
                )
                probes.extend(upload_metadata_update_probes)
            if not any(probe.status == "fail" for probe in probes):
                state_write_probe = _write_curator_state_safely(state_path, proposed_state)
                probes.append(state_write_probe)
                checkpoint_advanced = state_write_probe.status == "pass"
    elif mode == "dry_run":
        _record_feedback_plan_write(
            config.intake / "feedback" / "runs" / run_id,
            feedback_plan,
            feedback_plan_paths,
            probes,
        )
        _record_feedback_plan_write(
            config.output / "feedback" / "runs" / run_id,
            feedback_plan,
            feedback_plan_paths,
            probes,
        )
        _record_upload_plan_write(
            config.intake / "uploads" / "runs" / run_id,
            upload_plan,
            upload_plan_paths,
            probes,
        )
        _record_upload_plan_write(
            config.output / "uploads" / "runs" / run_id,
            upload_plan,
            upload_plan_paths,
            probes,
        )
    elif mode == "manual_live":
        _record_feedback_plan_write(
            config.intake / "feedback" / "runs" / run_id,
            feedback_plan,
            feedback_plan_paths,
            probes,
        )
        _record_feedback_plan_write(
            config.output / "feedback" / "runs" / run_id,
            feedback_plan,
            feedback_plan_paths,
            probes,
        )
        _record_upload_plan_write(
            config.intake / "uploads" / "runs" / run_id,
            upload_plan,
            upload_plan_paths,
            probes,
        )
        _record_upload_plan_write(
            config.output / "uploads" / "runs" / run_id,
            upload_plan,
            upload_plan_paths,
            probes,
        )
        if upload_review_validation_failure_count:
            probes.append(
                CuratorProbe(
                    name="manual-live-upload-pr",
                    status="fail",
                    message="upload PR creation skipped because upload-review validation failed",
                )
            )
        elif model_upload_review and upload_review_observations:
            live_execution_results = _execute_upload_review_prs(
                config=config,
                run_id=run_id,
                task_payload=task_payload,
                upload_plan=upload_plan,
                observations=upload_review_observations,
                outputs=upload_model_outputs,
            )
            probes.append(
                CuratorProbe(
                    name="manual-live-upload-pr",
                    status=(
                        "fail"
                        if any(result.status == "failed" for result in live_execution_results)
                        else "pass"
                    ),
                    message="manual_live upload PR execution completed",
                    details={
                        "result_count": len(live_execution_results),
                        "failed_count": sum(
                            1 for result in live_execution_results if result.status == "failed"
                        ),
                    },
                )
            )
        elif "repair_prs" not in enabled_actions:
            probes.append(
                CuratorProbe(
                    name="manual-live",
                    status="fail",
                    message="manual_live adapters are not enabled; policy preflight only was performed",
                )
            )
        if any(intent.operation != "pull.create" for intent in execution_intents):
            probes.append(
                CuratorProbe(
                    name="manual-live",
                    status="fail",
                    message="manual_live feedback issue/PR adapters are not enabled",
                )
            )

    feedback_count = _count_jsonl(feedback_path, "feedback", probes)
    query_log_count = _count_query_logs(config.logs, probes) if config.logs is not None else 0

    status = "fail" if any(probe.status == "fail" for probe in probes) else "pass"
    completed_at = datetime.now(UTC)
    report = CuratorDryRunReport(
        run_id=run_id,
        mode=mode,
        created_at=completed_at,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        task=task_payload,
        enabled_actions=sorted(enabled_actions),
        intake_path=str(config.intake),
        logs_path=str(config.logs) if config.logs is not None else None,
        output_path=str(config.output),
        lock_path=str(lock_path),
        feedback_window=feedback_window.model_dump(),
        feedback_checkpoint={
            "path": state.feedback_checkpoint.path,
            "previous_byte_offset": feedback_window.start_offset,
            "next_byte_offset": feedback_window.end_offset,
        },
        checkpoint_advanced=checkpoint_advanced,
        feedback_plan_paths=feedback_plan_paths,
        included_feedback_ids=feedback_plan.included_feedback_ids,
        feedback_decision_count=len(latest_decisions),
        feedback_decisions_appended=decisions_appended,
        upload_plan_paths=upload_plan_paths,
        included_upload_ids=upload_plan.included_upload_ids,
        upload_queue_counts=queue_snapshot.counts,
        pending_uploads=queue_snapshot.pending_uploads,
        upload_bundles=[bundle.model_dump(mode="json") for bundle in queue_snapshot.bundles],
        proposed_actions=[
            action.model_dump(mode="json") for action in feedback_plan.proposed_actions
        ],
        proposed_action_count=len(feedback_plan.proposed_actions),
        upload_proposed_actions=[
            action.model_dump(mode="json") for action in upload_plan.proposed_actions
        ],
        upload_proposed_action_count=len(upload_plan.proposed_actions),
        upload_review_previews=[
            preview.model_dump(mode="json") for preview in upload_plan.review_previews
        ],
        upload_review_preview_count=len(upload_plan.review_previews),
        upload_review_observations=[
            observation.model_dump(mode="json") for observation in upload_review_observations
        ],
        upload_review_observation_count=len(upload_review_observations),
        upload_review_validation_failure_count=upload_review_validation_failure_count,
        pr_repair_results=[result.model_dump(mode="json") for result in pr_repair_results],
        pr_repair_result_count=len(pr_repair_results),
        pr_repair_validation_failure_count=sum(
            1 for result in pr_repair_results if result.status == "validation_failed"
        ),
        upload_metadata_update_count=len(upload_metadata_update_paths),
        upload_metadata_update_paths=upload_metadata_update_paths,
        referenced_upload_ids=sorted(
            set(feedback_plan.referenced_upload_ids) | set(upload_plan.included_upload_ids)
        ),
        referenced_source_ids=feedback_plan.referenced_source_ids,
        referenced_section_ids=feedback_plan.referenced_section_ids,
        referenced_result_ids=feedback_plan.referenced_result_ids,
        github_mutation_budget=github_mutation_budget,
        policy_decisions=[decision.model_dump(mode="json") for decision in policy_decisions],
        policy_denial_count=policy_denial_count,
        execution_intents=[intent.model_dump(mode="json") for intent in execution_intents],
        execution_intent_count=len(execution_intents),
        simulated_execution_results=[
            result.model_dump(mode="json")
            for result in [
                *simulated_execution_results,
                *live_execution_results,
                *pr_repair_handoff_results,
            ]
        ],
        simulated_execution_count=len(simulated_execution_results),
        executed_action_count=sum(1 for result in live_execution_results if result.status == "executed")
        + sum(1 for result in pr_repair_results if result.pushed)
        + sum(1 for result in pr_repair_handoff_results if result.status == "executed"),
        github_mutation_count=sum(1 for result in live_execution_results if result.status == "executed")
        + sum(1 for result in pr_repair_results if result.pushed)
        + sum(1 for result in pr_repair_handoff_results if result.status == "executed"),
        capacity_deferral_count=sum(
            1 for action in feedback_plan.proposed_actions if action.classification == "capacity"
        ),
        capacity_deferred_feedback_ids=feedback_plan.capacity_deferred_feedback_ids,
        model_call_budget=model_call_budget,
        model_call_count=model_call_count,
        model_token_count=model_token_count,
        validation_failure_count=metadata_error_count
        + manifest_error_count
        + branch_collision_count
        + input_error_count
        + upload_review_validation_failure_count
        + sum(1 for result in pr_repair_results if result.status == "validation_failed"),
        input_error_count=input_error_count,
        input_errors=input_errors,
        feedback_count=feedback_count,
        query_log_count=query_log_count,
        model_budget_exhausted=model_budget_exhausted,
        reconciliation=reconciliation.model_dump(mode="json"),
        partial_failures=_partial_failures(probes),
        probes=probes,
        proposed_state=proposed_state.model_dump(mode="json"),
    )
    write_curator_reports(report, config.output)
    return report


def _empty_report(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
    *,
    status: Literal["pass", "fail"],
    run_id: str,
    mode: str,
    started_at: datetime,
    task_payload: dict[str, Any] | None,
    lock_path: Path,
    enabled_actions: set[str],
    feedback_soft_action_threshold: int,
    github_mutation_budget: dict[str, int],
    model_call_budget: dict[str, int],
    model_feedback_planning: bool,
    feedback_model: str | None,
    model_upload_review: bool,
    upload_review_model: str | None,
    pr_repair_executor: str | None,
    pr_repair_model: str,
    pr_repair_max_per_run: int,
    pr_repair_validation_command: list[str],
) -> CuratorDryRunReport:
    completed_at = datetime.now(UTC)
    return CuratorDryRunReport(
        run_id=run_id,
        mode=mode,
        created_at=completed_at,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        task=task_payload,
        enabled_actions=sorted(enabled_actions),
        intake_path=str(config.intake),
        logs_path=str(config.logs) if config.logs is not None else None,
        output_path=str(config.output),
        lock_path=str(lock_path),
        feedback_window={"start_offset": 0, "end_offset": 0},
        feedback_checkpoint={
            "path": "feedback/feedback.jsonl",
            "previous_byte_offset": 0,
            "next_byte_offset": 0,
        },
        checkpoint_advanced=False,
        feedback_plan_paths=[],
        included_feedback_ids=[],
        feedback_decision_count=0,
        feedback_decisions_appended=0,
        upload_plan_paths=[],
        included_upload_ids=[],
        upload_queue_counts={name: 0 for name in ("pending", "claimed", "processed", "rejected", "archive", "deferred")},
        pending_uploads=[],
        upload_bundles=[],
        proposed_actions=[],
        proposed_action_count=0,
        upload_proposed_actions=[],
        upload_proposed_action_count=0,
        upload_review_previews=[],
        upload_review_preview_count=0,
        upload_review_observations=[],
        upload_review_observation_count=0,
        upload_review_validation_failure_count=0,
        pr_repair_results=[],
        pr_repair_result_count=0,
        pr_repair_validation_failure_count=0,
        upload_metadata_update_count=0,
        upload_metadata_update_paths=[],
        referenced_upload_ids=[],
        referenced_source_ids=[],
        referenced_section_ids=[],
        referenced_result_ids=[],
        github_mutation_budget=github_mutation_budget,
        policy_decisions=[],
        policy_denial_count=0,
        execution_intents=[],
        execution_intent_count=0,
        simulated_execution_results=[],
        simulated_execution_count=0,
        capacity_deferred_feedback_ids=[],
        validation_failure_count=0,
        input_error_count=0,
        input_errors=[],
        model_call_budget=model_call_budget,
        feedback_count=0,
        query_log_count=0,
        reconciliation={},
        partial_failures=_partial_failures(probes),
        probes=probes,
    )


def write_curator_reports(report: CuratorDryRunReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "run-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "run-report.md").write_text(_report_markdown(report), encoding="utf-8")


def _write_feedback_plan(runs_dir: Path, plan: FeedbackPlan) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / "feedback-plan.json"
    path.write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _write_upload_plan(runs_dir: Path, plan: UploadPlan) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / "upload-plan.json"
    path.write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _record_feedback_plan_write(
    runs_dir: Path,
    plan: FeedbackPlan,
    paths: list[str],
    probes: list[CuratorProbe],
) -> None:
    try:
        paths.append(str(_write_feedback_plan(runs_dir, plan)))
    except Exception as exc:  # noqa: BLE001 - plan write failures must be reported.
        probes.append(
            CuratorProbe(
                name="feedback-plan-write",
                status="fail",
                message=f"feedback plan write failed: {exc}",
                details={"path": str(runs_dir / "feedback-plan.json")},
            )
        )


def _record_upload_plan_write(
    runs_dir: Path,
    plan: UploadPlan,
    paths: list[str],
    probes: list[CuratorProbe],
) -> None:
    try:
        paths.append(str(_write_upload_plan(runs_dir, plan)))
    except Exception as exc:  # noqa: BLE001 - plan write failures must be reported.
        probes.append(
            CuratorProbe(
                name="upload-plan-write",
                status="fail",
                message=f"upload plan write failed: {exc}",
                details={"path": str(runs_dir / "upload-plan.json")},
            )
        )


def _partial_failures(probes: list[CuratorProbe]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for probe in probes:
        if probe.status != "fail":
            continue
        failures.append(
            {
                "name": probe.name,
                "message": probe.message[:500],
                "details": probe.details,
            }
        )
    return failures[:20]


def _state_only_preflight_failure_count(probes: list[CuratorProbe]) -> int:
    blocking_probe_names = {
        "broker",
        "broker-preflight",
        "broker-pr-read",
        "broker-issue-read",
        "broker-upload-preflight",
        "fixture-execution",
        "model-budget",
        "model-proxy",
    }
    return sum(
        1
        for probe in probes
        if probe.status == "fail" and probe.name in blocking_probe_names
    )


def _read_task(
    path: Path | None, probes: list[CuratorProbe]
) -> tuple[dict[str, Any] | None, CuratorTask | None]:
    if path is None:
        probes.append(CuratorProbe(name="task", status="skip", message="no task path configured"))
        return None, None
    if not path.exists():
        probes.append(CuratorProbe(name="task", status="fail", message=f"task file missing: {path}"))
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        probes.append(CuratorProbe(name="task", status="fail", message=f"task file unreadable: {exc}"))
        return None, None
    if not isinstance(payload, dict):
        probes.append(CuratorProbe(name="task", status="fail", message="task JSON must be an object"))
        return None, None
    if _is_broker_task_contract(payload):
        task_payload, task, message = parse_curator_task_payload(payload)
        if task is not None:
            probes.append(CuratorProbe(name="task", status="pass", message=message))
            return task_payload, task
        probes.append(CuratorProbe(name="task", status="fail", message=message))
        return payload, None
    try:
        task = CuratorTask.model_validate(payload)
    except ValidationError as exc:
        contract_keys = {
            "schema_version",
            "mode",
            "enabled_actions",
            "github_mutation_budget",
            "model_call_budget",
            "feedback_soft_action_threshold",
            "stale_lock_timeout_seconds",
            "model_feedback_planning",
            "feedback_model",
            "model_upload_review",
            "upload_review_model",
            "pr_repair_executor",
            "pr_repair_model",
            "pr_repair_max_per_run",
            "pr_repair_validation_command",
        }
        if contract_keys.intersection(payload):
            probes.append(
                CuratorProbe(name="task", status="fail", message=f"task contract invalid: {exc}")
            )
        else:
            probes.append(
                CuratorProbe(
                    name="task",
                    status="pass",
                    message="legacy task JSON loaded without Curator contract fields",
                )
            )
        return payload, None
    probes.append(CuratorProbe(name="task", status="pass", message="task contract loaded"))
    return payload, task


def parse_curator_task_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], CuratorTask | None, str]:
    if not _is_broker_task_contract(payload):
        task = CuratorTask.model_validate(payload)
        return payload, task, "task contract loaded"

    embedded = payload.get("task")
    if not isinstance(embedded, str):
        return payload, None, "broker task contract must contain a string task field"
    try:
        embedded_payload = json.loads(embedded)
    except json.JSONDecodeError as exc:
        return payload, None, f"broker task string must contain Curator task JSON: {exc}"
    if not isinstance(embedded_payload, dict):
        return payload, None, "broker task string must contain a Curator task JSON object"
    broker_run_id = payload.get("run_id")
    if isinstance(broker_run_id, str):
        embedded_payload = dict(embedded_payload)
        if embedded_payload.get("run_id") in (
            None,
            "",
            "$SANDBOX_RUN_ID",
            "${SANDBOX_RUN_ID}",
            "$BROKER_RUN_ID",
            "${BROKER_RUN_ID}",
        ):
            embedded_payload["run_id"] = broker_run_id
    try:
        task = CuratorTask.model_validate(embedded_payload)
    except ValidationError as exc:
        return embedded_payload, None, f"embedded Curator task contract invalid: {exc}"
    if isinstance(broker_run_id, str) and task.run_id != broker_run_id:
        return (
            embedded_payload,
            None,
            f"embedded Curator task run_id {task.run_id!r} does not match broker run_id {broker_run_id!r}",
        )
    return embedded_payload, task, "broker task contract loaded with embedded Curator task"


def _is_broker_task_contract(payload: dict[str, Any]) -> bool:
    broker_keys = {
        "task",
        "repo",
        "base_branch",
        "branch",
        "worker_agent_id",
        "broker_remote_url",
    }
    return broker_keys.issubset(payload)


def _check_directory(path: Path, name: str, probes: list[CuratorProbe], *, writable: bool) -> None:
    if writable:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            probes.append(CuratorProbe(name=name, status="fail", message=f"path cannot be created: {exc}"))
            return
    if not path.exists():
        probes.append(CuratorProbe(name=name, status="fail", message=f"path does not exist: {path}"))
        return
    if not path.is_dir():
        probes.append(CuratorProbe(name=name, status="fail", message=f"path is not a directory: {path}"))
        return
    if writable:
        try:
            marker = path / ".ykm-curator-dry-run-write-test"
            marker.write_text("ok\n", encoding="utf-8")
            marker.unlink()
        except OSError as exc:
            probes.append(CuratorProbe(name=name, status="fail", message=f"path is not writable: {exc}"))
            return
    probes.append(CuratorProbe(name=name, status="pass", message=f"path is available: {path}"))


def _count_jsonl(path: Path, name: str, probes: list[CuratorProbe]) -> int:
    if not path.exists():
        probes.append(CuratorProbe(name=name, status="skip", message=f"JSONL file absent: {path}"))
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                json.loads(line)
                count += 1
    except (OSError, json.JSONDecodeError) as exc:
        probes.append(CuratorProbe(name=name, status="fail", message=f"JSONL read failed: {exc}"))
        return count
    probes.append(CuratorProbe(name=name, status="pass", message=f"{count} JSONL records readable"))
    return count


def _count_query_logs(logs: Path | None, probes: list[CuratorProbe]) -> int:
    if logs is None:
        return 0
    return _count_jsonl(logs / "query-log.jsonl", "query-log", probes)


def _check_forbidden_env(probes: list[CuratorProbe]) -> None:
    present = [name for name in DEFAULT_FORBIDDEN_ENV if os.getenv(name)]
    if present:
        probes.append(
            CuratorProbe(
                name="forbidden-env",
                status="fail",
                message="forbidden secret environment variables are present",
                details={"names": present},
            )
        )
    else:
        probes.append(
            CuratorProbe(
                name="forbidden-env",
                status="pass",
                message="no forbidden provider/GitHub/VPS secret environment variables found",
            )
        )


def _requires_broker(feedback_plan: FeedbackPlan, upload_plan: UploadPlan) -> bool:
    return any(
        action.action_type in {"issue", "corpus_pr"} for action in feedback_plan.proposed_actions
    ) or bool(upload_plan.review_previews)


def _apply_model_feedback_planning(
    *,
    config: CuratorDryRunConfig,
    run_id: str,
    model: str | None,
    model_call_budget: ModelCallBudget,
    base_plan: FeedbackPlan,
    feedback_records: list[dict[str, Any]],
) -> tuple[FeedbackPlan, CuratorProbe, dict[str, int]]:
    empty_usage = {"call_count": 0, "token_count": 0}
    if not base_plan.included_feedback_ids:
        return (
            base_plan,
            CuratorProbe(
                name="model-feedback-planning",
                status="skip",
                message="model feedback planning skipped because no feedback records are included",
            ),
            empty_usage,
        )
    if model_call_budget.max_calls_per_run < 1:
        return (
            base_plan,
            CuratorProbe(
                name="model-feedback-planning",
                status="fail",
                message="model feedback planning requires at least one model call",
            ),
            empty_usage,
        )
    if not model:
        return (
            base_plan,
            CuratorProbe(
                name="model-feedback-planning",
                status="fail",
                message="model feedback planning requires feedback_model in the task contract",
            ),
            empty_usage,
        )
    response = None
    try:
        adapter = _model_adapter(config)
        request = _feedback_planning_model_request(
            run_id=run_id,
            model=model,
            model_call_budget=model_call_budget,
            base_plan=base_plan,
            feedback_records=feedback_records,
        )
        response = adapter.call(request)
        output = validate_model_response_output(
            response,
            FeedbackPlanningModelOutput,
            expected_task_name="feedback_plan",
        )
        proposed_actions = build_feedback_planning_proposed_actions(output, base_plan=base_plan)
    except Exception as exc:  # noqa: BLE001 - model failures must fail closed into the report.
        usage = empty_usage
        details: dict[str, Any] = {"model": model, "error": str(exc)}
        if response is not None:
            usage = {
                "call_count": 1,
                "token_count": response.usage.input_tokens + response.usage.output_tokens,
            }
            details.update(
                {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
            )
            if isinstance(response.output, dict):
                proposed_actions = response.output.get("proposed_actions")
                if isinstance(proposed_actions, list):
                    details["proposed_action_count"] = len(proposed_actions)
        return (
            base_plan,
            CuratorProbe(
                name="model-feedback-planning",
                status="fail",
                message=f"model feedback planning failed: {exc}",
                details=details,
            ),
            usage,
        )
    token_count = response.usage.input_tokens + response.usage.output_tokens
    return (
        base_plan.model_copy(update={"proposed_actions": proposed_actions}),
        CuratorProbe(
            name="model-feedback-planning",
            status="pass",
            message="model feedback planning completed",
            details={
                "model": model,
                "proposed_action_count": len(output.proposed_actions),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        ),
        {"call_count": 1, "token_count": token_count},
    )


def _model_adapter(config: CuratorDryRunConfig) -> FixtureModelAdapter | HttpModelProxyAdapter:
    if config.model_proxy_fixture is not None:
        return FixtureModelAdapter.from_path(config.model_proxy_fixture)
    return HttpModelProxyAdapter(
        config.model_proxy_url or "",
        token=config.model_proxy_token,
    )


def _feedback_planning_model_request(
    *,
    run_id: str,
    model: str,
    model_call_budget: ModelCallBudget,
    base_plan: FeedbackPlan,
    feedback_records: list[dict[str, Any]],
) -> ModelCallRequest:
    included = set(base_plan.included_feedback_ids)
    records = []
    for raw_record in feedback_records:
        try:
            record = FeedbackInputRecord.model_validate(raw_record)
        except ValidationError:
            continue
        if record.feedback_id not in included:
            continue
        comment = raw_record.get("comment")
        records.append(
            {
                "feedback_id": record.feedback_id,
                "category": record.category,
                "comment": str(comment)[:2000] if comment is not None else "",
                "source_id": record.source_id,
                "section_id": record.section_id,
                "result_ids": record.result_ids,
                "upload_id": record.upload_id,
            }
        )
    prompt_input = {
        "schema_version": "1",
        "run_id": run_id,
        "feedback_window": base_plan.feedback_window.model_dump(),
        "feedback_records": records,
        "constraints": [
            "Return only valid JSON matching the response schema.",
            "Use only durable evidence identifiers present in feedback_records.",
            "Do not propose GitHub mutations for positive or non-actionable feedback.",
            "Use action_type no_action, issue, corpus_pr, link_to_upload, or defer.",
            "Use classification positive, non_actionable, owner_action, corpus_candidate, upload_linked, capacity, or insufficient_evidence.",
            "Do not include action_id, idempotency_key, validation, or execution fields; the controller assigns them.",
            "Cover every included feedback_id in at least one proposed action.",
            "A feedback_id is durable evidence for issue, no_action, and defer actions.",
            "Use issue with classification owner_action for needs_owner_action feedback.",
            "Use issue with classification owner_action for untargeted missing_content, wrong_content, stale_content, and unclear_content feedback.",
            "Use corpus_pr with classification corpus_candidate for missing_content, wrong_content, stale_content, and unclear_content feedback when source_id, section_id, or upload_id evidence identifies the corpus target.",
            "Use no_action with classification non_actionable for agent_note and non_actionable feedback.",
            "Use no_action with classification positive for positive_content feedback.",
            "Use corpus_pr only with source_id, section_id, or upload_id evidence.",
            "Use link_to_upload only with upload_id evidence.",
            f"Use target_repo {DEFAULT_CORPUS_REPO} for issue and corpus_pr actions.",
            "Use defer only when a record cannot be safely classified from its category and evidence.",
            "Classify agent_note, non_actionable, and positive_content as no_action unless durable evidence proves otherwise.",
            "Prefer one grouped action over many identical actions when action_type, classification, target_repo, and evidence kind match.",
        ],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are the YouKnowMe Curator planning model. Produce conservative "
                "feedback actions from durable evidence only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(prompt_input, sort_keys=True),
        },
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "feedback_planning_output",
            "schema": strict_model_json_schema(FeedbackPlanningModelOutput),
            "strict": True,
        },
    }
    max_tokens = model_call_budget.max_tokens_per_run or None
    return ModelCallRequest(
        task_name="feedback_plan",
        run_id=run_id,
        model=model,
        input={
            "messages": messages,
            "response_format": response_format,
            "temperature": 0,
            "metadata": {"feature": "feedback_planning"},
        },
        max_tokens=max_tokens,
    )


def _apply_model_upload_review(
    *,
    config: CuratorDryRunConfig,
    upload_plan: UploadPlan,
    upload_snapshot: UploadQueueSnapshot,
    model: str | None,
    model_call_budget: ModelCallBudget,
    used_model_calls: int,
) -> tuple[list[UploadReviewObservation], dict[str, UploadReviewModelOutput], CuratorProbe, dict[str, int]]:
    empty_usage = {"call_count": 0, "token_count": 0}
    if not upload_plan.review_previews:
        return (
            [],
            {},
            CuratorProbe(
                name="model-upload-review",
                status="skip",
                message="model upload review skipped because no upload review previews are included",
            ),
            empty_usage,
        )
    if not model:
        return (
            [],
            {},
            CuratorProbe(
                name="model-upload-review",
                status="fail",
                message="model upload review requires upload_review_model in the task contract",
            ),
            empty_usage,
        )
    if config.corpus_checkout is None:
        return (
            [],
            {},
            CuratorProbe(
                name="model-upload-review",
                status="fail",
                message="model upload review requires a corpus checkout for validation observe",
            ),
            empty_usage,
        )
    remaining_calls = model_call_budget.max_calls_per_run - used_model_calls
    required_calls = len(upload_plan.review_previews)
    if remaining_calls < required_calls:
        return (
            [],
            {},
            CuratorProbe(
                name="model-upload-review",
                status="fail",
                message="model upload review requires one model call per upload review preview",
                details={"required_calls": required_calls, "remaining_calls": max(remaining_calls, 0)},
            ),
            empty_usage,
        )

    observations: list[UploadReviewObservation] = []
    outputs: dict[str, UploadReviewModelOutput] = {}
    call_count = 0
    token_count = 0
    bundles_by_upload = {bundle.upload_id: bundle for bundle in upload_snapshot.bundles}
    for preview in upload_plan.review_previews:
        bundle = bundles_by_upload.get(preview.upload_id)
        if bundle is None:
            observations.append(
                UploadReviewObservation(
                    upload_id=preview.upload_id,
                    action_id=preview.action_id,
                    status="fail",
                    message="upload review bundle disappeared before model review",
                )
            )
            continue
        response = None
        try:
            request = _upload_review_model_request(
                run_id=upload_plan.run_id,
                model=model,
                model_call_budget=model_call_budget,
                bundle=bundle,
            )
            call_count += 1
            response = _model_adapter(config).call(request)
            output = validate_model_response_output(
                response,
                UploadReviewModelOutput,
                expected_task_name="upload_review",
            )
            if output.upload_id != preview.upload_id:
                raise ValueError(
                    f"model upload review returned upload_id {output.upload_id!r} "
                    f"for {preview.upload_id!r}"
                )
            outputs[preview.upload_id] = output
            observations.append(
                observe_upload_review_draft(
                    corpus_checkout=config.corpus_checkout,
                    output=output,
                    action_id=preview.action_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 - model/observe failures must fail closed.
            observations.append(
                UploadReviewObservation(
                    upload_id=preview.upload_id,
                    action_id=preview.action_id,
                    status="fail",
                    message=f"model upload review failed: {exc}",
                )
            )
        finally:
            if response is not None:
                token_count += response.usage.input_tokens + response.usage.output_tokens

    failures = [observation for observation in observations if observation.status == "fail"]
    passes = [observation for observation in observations if observation.status == "pass"]
    skips = [observation for observation in observations if observation.status == "skip"]
    return (
        observations,
        outputs,
        CuratorProbe(
            name="model-upload-review",
            status="fail" if failures else "pass",
            message=(
                f"model upload review observed {len(passes)} passing, "
                f"{len(failures)} failing, and {len(skips)} skipped drafts"
            ),
            details={
                "model": model,
                "observation_count": len(observations),
                "pass_count": len(passes),
                "fail_count": len(failures),
                "skip_count": len(skips),
            },
        ),
        {"call_count": call_count, "token_count": token_count},
    )


def _upload_review_execution_intents(
    *,
    run_id: str,
    upload_plan: UploadPlan,
    observations: list[UploadReviewObservation],
) -> list[ExecutionIntent]:
    passing_upload_ids = {
        observation.upload_id for observation in observations if observation.status == "pass"
    }
    return [
        upload_review_pull_intent(run_id=run_id, preview=preview)
        for preview in upload_plan.review_previews
        if preview.upload_id in passing_upload_ids
    ]


def _execute_upload_review_prs(
    *,
    config: CuratorDryRunConfig,
    run_id: str,
    task_payload: dict[str, Any] | None,
    upload_plan: UploadPlan,
    observations: list[UploadReviewObservation],
    outputs: dict[str, UploadReviewModelOutput],
) -> list[ExecutionResult]:
    if config.corpus_checkout is None:
        results = []
        for preview in upload_plan.review_previews:
            intent = upload_review_pull_intent(run_id=run_id, preview=preview)
            results.append(
                ExecutionResult(
                    action_id=intent.action_id,
                    operation=intent.operation,
                    idempotency_key=intent.idempotency_key,
                    status="failed",
                    target_repo=intent.target_repo,
                    branch=intent.branch,
                    message="corpus checkout is required for upload PR execution",
                )
            )
        return results
    broker_remote_url = _broker_remote_url(config, task_payload)
    adapter = (
        FixtureBrokerAdapter.from_path(config.broker_fixture)
        if config.broker_fixture is not None
        else HttpBrokerAdapter(config.broker_url or "")
    )
    previews_by_upload = {preview.upload_id: preview for preview in upload_plan.review_previews}
    results = []
    for observation in observations:
        if observation.status != "pass":
            continue
        preview = previews_by_upload.get(observation.upload_id)
        output = outputs.get(observation.upload_id)
        if preview is None or output is None:
            continue
        results.append(
            execute_upload_review_pr(
                run_id=run_id,
                broker_remote_url=broker_remote_url,
                broker_adapter=adapter,
                preview=preview,
                output=output,
            )
        )
    return results


def _complete_pr_repair_handoffs(
    *,
    config: CuratorDryRunConfig,
    results: list[PrRepairResult],
    snapshots: list[CuratorPrSnapshot],
) -> list[ExecutionResult]:
    adapter = (
        FixtureBrokerAdapter.from_path(config.broker_fixture)
        if config.broker_fixture is not None
        else HttpBrokerAdapter(config.broker_url or "")
    )
    snapshots_by_number = {snapshot.number: snapshot for snapshot in snapshots}
    handoff_results: list[ExecutionResult] = []
    for result in results:
        if not result.pushed or not result.review_request_comment:
            continue
        snapshot = snapshots_by_number.get(result.pr_number)
        action_id = f"pr_repair_comment_{result.pr_number}"
        idempotency_key = f"pr-repair-comment:{result.pr_number}:{result.branch or 'unknown'}"
        comment_result = adapter.add_issue_comment(
            target_repo=DEFAULT_CORPUS_REPO,
            issue_number=result.pr_number,
            body=result.review_request_comment,
            action_id=action_id,
            idempotency_key=idempotency_key,
        )
        result.review_request_comment_status = (
            "posted" if comment_result.status != "failed" else "failed"
        )
        result.review_request_comment_message = comment_result.message
        handoff_results.append(comment_result)

        for review in (snapshot.reviews if snapshot else []):
            if review.state.lower() != "changes_requested":
                continue
            review_id = str(review.database_id or review.id or "")
            if not review_id:
                continue
            dismiss_result = adapter.dismiss_pull_review(
                target_repo=DEFAULT_CORPUS_REPO,
                pr_number=result.pr_number,
                review_id=review_id,
                message=_repair_resolution_message(result),
                action_id=f"pr_repair_dismiss_review_{result.pr_number}_{review_id}",
                idempotency_key=(
                    f"pr-repair-dismiss-review:{result.pr_number}:{result.branch or 'unknown'}:"
                    f"{review_id}"
                ),
            )
            if dismiss_result.status != "failed":
                result.dismissed_review_count += 1
            handoff_results.append(dismiss_result)

        for thread in (snapshot.review_threads if snapshot else []):
            if thread.is_resolved or not thread.id:
                continue
            resolve_result = adapter.resolve_review_thread(
                target_repo=DEFAULT_CORPUS_REPO,
                pr_number=result.pr_number,
                thread_id=thread.id,
                message=_repair_resolution_message(result),
                action_id=f"pr_repair_resolve_thread_{result.pr_number}_{thread.id}",
                idempotency_key=(
                    f"pr-repair-resolve-thread:{result.pr_number}:{result.branch or 'unknown'}:"
                    f"{thread.id}"
                ),
            )
            if resolve_result.status != "failed":
                result.resolved_thread_count += 1
            handoff_results.append(resolve_result)

        labels = set(snapshot.labels if snapshot else [])
        if CURATOR_WAITING_REVIEW_LABEL not in labels:
            label_result = adapter.add_issue_label(
                target_repo=DEFAULT_CORPUS_REPO,
                issue_number=result.pr_number,
                label=CURATOR_WAITING_REVIEW_LABEL,
                action_id=f"pr_repair_add_waiting_review_{result.pr_number}",
                idempotency_key=(
                    f"pr-repair-label-add:{result.pr_number}:{result.branch or 'unknown'}:"
                    f"{CURATOR_WAITING_REVIEW_LABEL}"
                ),
            )
            if label_result.status != "failed":
                result.label_update_count += 1
            handoff_results.append(label_result)
        if CURATOR_NEEDS_WORK_LABEL in labels:
            label_result = adapter.remove_issue_label(
                target_repo=DEFAULT_CORPUS_REPO,
                issue_number=result.pr_number,
                label=CURATOR_NEEDS_WORK_LABEL,
                action_id=f"pr_repair_remove_needs_work_{result.pr_number}",
                idempotency_key=(
                    f"pr-repair-label-remove:{result.pr_number}:{result.branch or 'unknown'}:"
                    f"{CURATOR_NEEDS_WORK_LABEL}"
                ),
            )
            if label_result.status != "failed":
                result.label_update_count += 1
            handoff_results.append(label_result)
    return handoff_results


def _repair_resolution_message(result: PrRepairResult) -> str:
    return (
        "Dismissed after Curator repair push.\n\n"
        f"What was fixed: {', '.join(result.changed_files) or 'Curator branch changes'}.\n\n"
        "Why it broke: the original PR did not fully account for the validation and policy impact "
        "of the requested corpus changes.\n\n"
        "How it should not break again: the Curator repair path validates the repaired branch before "
        "pushing, treats missing validation as actionable, and posts an explicit owner-review handoff."
    )


def _broker_remote_url(config: CuratorDryRunConfig, task_payload: dict[str, Any] | None) -> str:
    task_remote_url = _task_broker_remote_url(task_payload)
    if task_remote_url:
        return task_remote_url
    if config.broker_url:
        return f"{config.broker_url.rstrip('/')}/git/{DEFAULT_CORPUS_REPO}.git"
    return ""


def _task_broker_remote_url(task_payload: dict[str, Any] | None) -> str | None:
    if task_payload is not None:
        broker_remote_url = task_payload.get("broker_remote_url")
        if isinstance(broker_remote_url, str) and broker_remote_url:
            return broker_remote_url
    return None


def _upload_review_model_request(
    *,
    run_id: str,
    model: str,
    model_call_budget: ModelCallBudget,
    bundle: Any,
) -> ModelCallRequest:
    manifest = _read_upload_manifest_for_model(Path(bundle.path))
    files = _read_upload_files_for_model(Path(bundle.path))
    prompt_input = {
        "schema_version": "1",
        "run_id": run_id,
        "upload_id": bundle.upload_id,
        "manifest": manifest,
        "files": files,
        "corpus_policy": {
            "allowed_types": sorted(ALLOWED_TYPES),
            "allowed_tags": sorted(ALLOWED_TAGS),
            "corpus_roots": [
                "homemaint",
                "preferences",
                "skills",
                "substack",
                "workhistory",
                "writingsamples",
            ],
        },
        "constraints": [
            "Return only valid JSON matching the response schema.",
            "Produce reviewable corpus markdown files; do not merge or publish anything.",
            "Every output file must be complete markdown with a frontmatter block whose delimiter lines are exactly three hyphens: ---.",
            "Output frontmatter may contain only id, type, tags, aliases, and related; choose the corpus root through the file path, not a root frontmatter field.",
            "Prefer existing corpus types and tags when they fit.",
            "When existing vocabulary does not fit, propose a small policy_patch instead of misclassifying the document.",
            "Do not weaken validation limits, remove existing policy values, or include secrets.",
            "Use decision integrated only when files contain normalized corpus markdown for review.",
            "Use decision needs_owner_action when the upload cannot be safely normalized from the supplied context.",
            "Keep rationale short and state why any policy additions are needed.",
        ],
    }
    return ModelCallRequest(
        task_name="upload_review",
        run_id=run_id,
        model=model,
        input={
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the YouKnowMe Curator upload-review model. Normalize staged "
                        "markdown into reviewable corpus files and propose minimal policy additions "
                        "when the current corpus vocabulary is missing needed terms."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_input, sort_keys=True)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "upload_review_output",
                    "schema": strict_model_json_schema(UploadReviewModelOutput),
                    "strict": True,
                },
            },
            "temperature": 0,
            "metadata": {"feature": "upload_review"},
        },
        max_tokens=model_call_budget.max_tokens_per_run or None,
    )


def _read_upload_manifest_for_model(bundle_path: Path) -> dict[str, Any]:
    path = bundle_path / "manifest.json"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if len(text) > MAX_UPLOAD_REVIEW_MANIFEST_CHARS:
        raise ValueError("upload manifest is too large for model review")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("upload manifest must be a JSON object")
    return payload


def _read_upload_files_for_model(bundle_path: Path) -> list[dict[str, str]]:
    files_dir = bundle_path / "files"
    if not files_dir.exists() or not files_dir.is_dir():
        raise ValueError("upload bundle has no files directory for model review")
    paths = sorted(path for path in files_dir.iterdir() if path.is_file())
    if len(paths) > MAX_UPLOAD_REVIEW_FILES:
        raise ValueError(f"upload bundle exceeds {MAX_UPLOAD_REVIEW_FILES} files for model review")
    files: list[dict[str, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if len(text) > MAX_UPLOAD_REVIEW_FILE_CHARS:
            raise ValueError(f"{path.name} is too large for model review")
        files.append({"filename": path.name, "content": text})
    if not files:
        raise ValueError("upload bundle contains no files for model review")
    return files


def _validate_feedback_planning_model_output(
    base_plan: FeedbackPlan,
    output: FeedbackPlanningModelOutput,
) -> None:
    validate_feedback_planning_model_output(base_plan, output)


def _state_only_unresolved_feedback_ids(
    plan: FeedbackPlan,
    decisions: list[FeedbackDecision],
) -> list[str]:
    decided = {decision.feedback_id for decision in decisions}
    return sorted(feedback_id for feedback_id in plan.included_feedback_ids if feedback_id not in decided)


def _append_feedback_decisions_safely(
    path: Path,
    decisions: list[Any],
) -> tuple[int, CuratorProbe | None]:
    try:
        appended = append_feedback_decisions(path, decisions)
    except Exception as exc:  # noqa: BLE001 - write failures must be reported, not crash the run.
        return (
            0,
            CuratorProbe(
                name="feedback-decision-append",
                status="fail",
                message=f"feedback decision append failed: {exc}",
                details={"path": str(path), "decision_count": len(decisions)},
            ),
        )
    if appended:
        return (
            appended,
            CuratorProbe(
                name="feedback-decision-append",
                status="pass",
                message="feedback decisions appended",
                details={"path": str(path), "decision_count": appended},
            ),
        )
    return 0, None


def _write_curator_state_safely(path: Path, state: CuratorState) -> CuratorProbe:
    try:
        write_curator_state(path, state)
    except Exception as exc:  # noqa: BLE001 - state write failures must still produce reports.
        return CuratorProbe(
            name="curator-state-write",
            status="fail",
            message=f"curator state write failed: {exc}",
            details={"path": str(path)},
        )
    return CuratorProbe(
        name="curator-state-write",
        status="pass",
        message="curator state written",
        details={"path": str(path)},
    )


def _apply_upload_transition_previews(
    *,
    run_id: str,
    upload_snapshot: UploadQueueSnapshot,
    previews: list[UploadTransitionPreview],
) -> tuple[list[str], list[CuratorProbe]]:
    bundles_by_upload = {bundle.upload_id: bundle for bundle in upload_snapshot.bundles}
    updated_paths: list[str] = []
    probes: list[CuratorProbe] = []
    for preview in previews:
        if preview.validation != "accepted":
            continue
        bundle = bundles_by_upload.get(preview.upload_id)
        if bundle is None or bundle.curator_metadata is None:
            continue
        metadata = bundle.curator_metadata
        if metadata.state != preview.from_state:
            continue
        metadata_path = Path(bundle.path) / "curator.json"
        if not metadata_path.exists():
            continue
        try:
            transitioned = transition_upload_metadata(
                metadata,
                desired_state=preview.to_state,
                run_id=run_id,
                decision=_decision_for_upload_transition(preview),
                pr_number=preview.pr_number,
                blocking_issue_number=preview.issue_number,
                blocking_reason=preview.reason if preview.to_state == "deferred" else None,
            )
            metadata_path.write_text(
                transitioned.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            probes.append(
                CuratorProbe(
                    name="upload-metadata-update",
                    status="fail",
                    message=f"upload metadata update failed: {exc}",
                    details={"upload_id": preview.upload_id, "path": str(metadata_path)},
                )
            )
            continue
        updated_paths.append(str(metadata_path))
    if updated_paths:
        probes.append(
            CuratorProbe(
                name="upload-metadata-update",
                status="pass",
                message="upload curator metadata updated in place",
                details={"count": len(updated_paths), "paths": updated_paths[:20]},
            )
        )
    return updated_paths, probes


def _decision_for_upload_transition(preview: UploadTransitionPreview) -> UploadDecision | None:
    if preview.to_state == "processed":
        return "integrated"
    if preview.to_state == "deferred":
        return "deferred"
    if preview.to_state == "rejected":
        return "rejected"
    return None


def _historical_reentry_ids(
    latest_decisions: dict[str, Any],
    window_records: list[dict[str, Any]],
) -> set[str]:
    window_feedback_ids = {
        record.get("feedback_id")
        for record in window_records
        if isinstance(record.get("feedback_id"), str)
    }
    return {
        feedback_id
        for feedback_id in ready_reentry_feedback_ids(latest_decisions)
        if feedback_id not in window_feedback_ids
    }


def _merge_feedback_records(
    window_records: list[dict[str, Any]],
    reentry_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(window_records)
    seen = {
        record.get("feedback_id")
        for record in merged
        if isinstance(record.get("feedback_id"), str)
    }
    for record in reentry_records:
        feedback_id = record.get("feedback_id")
        if not isinstance(feedback_id, str) or feedback_id in seen:
            continue
        merged.append(record)
        seen.add(feedback_id)
    return merged


def _known_issue_numbers(
    latest_decisions: dict[str, Any],
    upload_snapshot: Any,
) -> list[int]:
    issue_numbers: set[int] = set()
    for decision in latest_decisions.values():
        issue_number = getattr(decision, "issue_number", None)
        if isinstance(issue_number, int):
            issue_numbers.add(issue_number)
    for bundle in getattr(upload_snapshot, "bundles", []):
        metadata = getattr(bundle, "curator_metadata", None)
        if metadata is None:
            continue
        for issue_number in (metadata.issue_number, metadata.blocking_issue_number):
            if isinstance(issue_number, int):
                issue_numbers.add(issue_number)
    return sorted(issue_numbers)


def _load_broker_fixture_pr_snapshots(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
) -> list[CuratorPrSnapshot]:
    if config.broker_fixture is None:
        return []
    try:
        snapshots = FixtureBrokerAdapter.from_path(config.broker_fixture).pr_snapshots()
    except (OSError, ValidationError) as exc:
        probes.append(
            CuratorProbe(
                name="broker-pr-snapshots",
                status="fail",
                message=f"broker fixture PR snapshots unreadable: {exc}",
            )
        )
        return []
    if snapshots:
        probes.append(
            CuratorProbe(
                name="broker-pr-snapshots",
                status="pass",
                message="broker fixture PR snapshots loaded",
                details={"count": len(snapshots)},
            )
        )
    return snapshots


def _load_broker_fixture_issue_snapshots(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
) -> list[CuratorIssueSnapshot]:
    if config.broker_fixture is None:
        return []
    try:
        snapshots = FixtureBrokerAdapter.from_path(config.broker_fixture).issue_snapshots()
    except (OSError, ValidationError) as exc:
        probes.append(
            CuratorProbe(
                name="broker-issue-snapshots",
                status="fail",
                message=f"broker fixture issue snapshots unreadable: {exc}",
            )
        )
        return []
    if snapshots:
        probes.append(
            CuratorProbe(
                name="broker-issue-snapshots",
                status="pass",
                message="broker fixture issue snapshots loaded",
                details={"count": len(snapshots)},
            )
        )
    return snapshots


def _load_http_broker_pr_snapshots(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
) -> list[CuratorPrSnapshot]:
    if config.broker_url is None:
        probes.append(
            CuratorProbe(
                name="broker-pr-read",
                status="fail",
                message="broker URL is required when broker reads are enabled",
            )
        )
        return []
    snapshots, probe = HttpBrokerAdapter(config.broker_url).read_pr_snapshots(
        target_repo=DEFAULT_CORPUS_REPO,
    )
    probes.append(probe)
    return snapshots


def _load_http_broker_issue_snapshots(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
    *,
    issue_numbers: list[int],
) -> list[CuratorIssueSnapshot]:
    if config.broker_url is None:
        if issue_numbers:
            probes.append(
                CuratorProbe(
                    name="broker-issue-read",
                    status="fail",
                    message="broker URL is required when broker reads are enabled",
                )
            )
        return []
    snapshots, probe = HttpBrokerAdapter(config.broker_url).read_issue_snapshots(
        target_repo=DEFAULT_CORPUS_REPO,
        issue_numbers=issue_numbers,
    )
    if probe is not None:
        probes.append(probe)
    return snapshots


def _probe_broker(
    config: CuratorDryRunConfig, probes: list[CuratorProbe], *, required: bool | None = None
) -> None:
    required = config.required_broker if required is None else required
    if config.broker_fixture is not None:
        try:
            probes.append(FixtureBrokerAdapter.from_path(config.broker_fixture).probe(required=required))
        except (OSError, ValidationError) as exc:
            probes.append(
                CuratorProbe(
                    name="broker",
                    status="fail" if required else "skip",
                    message=f"broker fixture unreadable: {exc}",
                )
            )
        return
    missing = [
        name
        for name, value in {
            "BROKER_URL": config.broker_url,
            "BROKER_AGENT_ID": os.getenv("BROKER_AGENT_ID"),
            "BROKER_AGENT_SECRET": os.getenv("BROKER_AGENT_SECRET"),
        }.items()
        if not value
    ]
    if missing:
        probes.append(
            CuratorProbe(
                name="broker",
                status="fail" if required else "skip",
                message="broker probe not configured",
                details={"missing": missing},
            )
        )
        return
    assert config.broker_url is not None
    probes.append(HttpBrokerAdapter(config.broker_url).probe(required=required))


def _probe_model_proxy(
    config: CuratorDryRunConfig, probes: list[CuratorProbe], *, required: bool | None = None
) -> None:
    required = config.required_model_proxy if required is None else required
    if config.model_proxy_fixture is not None:
        try:
            probes.append(
                FixtureModelAdapter.from_path(config.model_proxy_fixture).probe(required=required)
            )
        except (OSError, ValidationError) as exc:
            probes.append(
                CuratorProbe(
                    name="model-proxy",
                    status="fail" if required else "skip",
                    message=f"model proxy fixture unreadable: {exc}",
                )
            )
        return
    proxy_url = config.model_proxy_url or config.codex_proxy_base_url or ""
    proxy_token = config.model_proxy_token or config.codex_proxy_token
    probes.append(HttpModelProxyAdapter(proxy_url, token=proxy_token).probe(required=required))


def _probe_model_budget(
    config: CuratorDryRunConfig,
    probes: list[CuratorProbe],
    model_call_budget: dict[str, int],
) -> None:
    if config.model_proxy_fixture is None:
        return
    budget = ModelCallBudget.model_validate(model_call_budget)
    if budget.max_calls_per_run == 0 and budget.max_tokens_per_run == 0:
        return
    try:
        probes.append(FixtureModelAdapter.from_path(config.model_proxy_fixture).budget_probe(budget))
    except (OSError, ValidationError) as exc:
        probes.append(
            CuratorProbe(
                name="model-budget",
                status="fail",
                message=f"model proxy fixture budget preflight failed: {exc}",
            )
        )


def _report_markdown(report: CuratorDryRunReport) -> str:
    lines = [
        "# YouKnowMe Curator Run Report",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Mode: `{report.mode}`",
        f"- Status: `{report.status}`",
        f"- Enabled actions: `{', '.join(report.enabled_actions) or 'none'}`",
        f"- Created: `{report.created_at.isoformat()}`",
        f"- Intake: `{report.intake_path}`",
        f"- Logs: `{report.logs_path or 'not configured'}`",
        f"- Output: `{report.output_path}`",
        f"- Lock: `{report.lock_path}`",
        f"- Feedback window: `{report.feedback_window['start_offset']}..{report.feedback_window['end_offset']}`",
        f"- Feedback checkpoint advanced: `{report.checkpoint_advanced}`",
        f"- Feedback records: `{report.feedback_count}`",
        f"- Included feedback IDs: `{len(report.included_feedback_ids)}`",
        f"- Feedback decisions: `{report.feedback_decision_count}`",
        f"- Query log records: `{report.query_log_count}`",
        f"- Proposed actions: `{report.proposed_action_count}`",
        f"- Upload proposed actions: `{report.upload_proposed_action_count}`",
        f"- Upload review previews: `{report.upload_review_preview_count}`",
        f"- Upload review observations: `{report.upload_review_observation_count}`",
        f"- Upload review validation failures: `{report.upload_review_validation_failure_count}`",
        f"- PR repair results: `{report.pr_repair_result_count}`",
        f"- PR repair validation failures: `{report.pr_repair_validation_failure_count}`",
        f"- Upload metadata updates: `{report.upload_metadata_update_count}`",
        f"- Executed actions: `{report.executed_action_count}`",
        f"- GitHub mutations: `{report.github_mutation_count}`",
        f"- GitHub mutation budget: `{report.github_mutation_budget}`",
        f"- Policy denials: `{report.policy_denial_count}`",
        f"- Execution intents: `{report.execution_intent_count}`",
        f"- Simulated executions: `{report.simulated_execution_count}`",
        f"- Capacity deferrals: `{report.capacity_deferral_count}`",
        f"- Capacity-deferred feedback IDs: `{len(report.capacity_deferred_feedback_ids)}`",
        f"- Validation failures: `{report.validation_failure_count}`",
        f"- Input errors: `{report.input_error_count}`",
        f"- Model calls: `{report.model_call_count}`",
        f"- Model call budget: `{report.model_call_budget}`",
        f"- Model tokens: `{report.model_token_count}`",
        f"- Model budget exhausted: `{report.model_budget_exhausted}`",
        f"- Reentered feedback: `{report.reconciliation.get('reentered_feedback_count', 0)}`",
        "",
        "## Upload Queues",
        "",
    ]
    for state, count in sorted(report.upload_queue_counts.items()):
        lines.append(f"- `{state}`: `{count}`")
    upload_manifest_errors = _upload_manifest_errors(report)
    if upload_manifest_errors:
        lines.extend(["", "## Upload Manifest Errors", ""])
        for bundle in upload_manifest_errors:
            lines.append(
                f"- `{bundle['upload_id']}` in `{bundle['queue']}`: "
                f"{bundle['manifest_error'][:200]}"
            )
    if report.upload_review_previews:
        lines.extend(["", "## Upload Review Previews", ""])
        for preview in report.upload_review_previews[:20]:
            draft_status = preview.get("draft_status", "not_evaluated")
            draft_paths = preview.get("draft_paths") or []
            draft_suffix = f"; draft `{draft_status}`"
            if draft_paths:
                draft_suffix += " -> " + ", ".join(f"`{path}`" for path in draft_paths[:3])
            elif preview.get("blocking_reason"):
                draft_suffix += f": {str(preview['blocking_reason'])[:160]}"
            lines.append(
                f"- `{preview['upload_id']}` in `{preview['queue']}`: "
                f"`{preview['current_state']}` -> `{preview['proposed_state']}` "
                f"on `{preview['branch']}`{draft_suffix}"
            )
    if report.upload_review_observations:
        lines.extend(["", "## Upload Review Observations", ""])
        for observation in report.upload_review_observations[:20]:
            draft_paths = observation.get("draft_paths") or []
            suffix = ""
            if draft_paths:
                suffix = " -> " + ", ".join(f"`{path}`" for path in draft_paths[:3])
            returncode = observation.get("returncode")
            code_suffix = f" (exit `{returncode}`)" if returncode is not None else ""
            lines.append(
                f"- `{observation['upload_id']}`: `{observation['status']}`{code_suffix} - "
                f"{str(observation['message'])[:200]}{suffix}"
            )
    if report.pr_repair_results:
        lines.extend(["", "## PR Repair Results", ""])
        for result in report.pr_repair_results[:20]:
            changed_files = result.get("changed_files") or []
            suffix = ""
            if changed_files:
                suffix = " -> " + ", ".join(f"`{path}`" for path in changed_files[:5])
            comment_status = result.get("review_request_comment_status")
            comment_suffix = (
                f" comment: `{comment_status}`"
                if comment_status and comment_status != "not_applicable"
                else ""
            )
            handoff_counts = []
            if result.get("dismissed_review_count"):
                handoff_counts.append(f"dismissed reviews: `{result['dismissed_review_count']}`")
            if result.get("resolved_thread_count"):
                handoff_counts.append(f"resolved threads: `{result['resolved_thread_count']}`")
            if result.get("label_update_count"):
                handoff_counts.append(f"label updates: `{result['label_update_count']}`")
            if handoff_counts:
                comment_suffix += " " + ", ".join(handoff_counts)
            lines.append(
                f"- PR `#{result['pr_number']}`: `{result['status']}` on "
                f"`{result.get('branch') or 'unknown'}` - "
                f"{str(result['message'])[:200]}{comment_suffix}{suffix}"
            )
    branch_previews = report.reconciliation.get("branch_previews", [])
    if branch_previews:
        lines.extend(["", "## Branch Previews", ""])
        for preview in branch_previews:
            lines.append(f"- `{preview['action_id']}`: `{preview['branch']}`")
    pr_reconciliations = report.reconciliation.get("pr_reconciliations", [])
    if pr_reconciliations:
        lines.extend(["", "## PR Reconciliation", ""])
        pr_state_counts = report.reconciliation.get("pr_state_counts", {})
        if isinstance(pr_state_counts, dict) and pr_state_counts:
            rendered_counts = ", ".join(
                f"`{state}`: `{count}`" for state, count in sorted(pr_state_counts.items())
            )
            lines.append(f"- State counts: {rendered_counts}")
        for item in pr_reconciliations[:20]:
            labels = item.get("labels") or []
            label_suffix = ""
            if isinstance(labels, list) and labels:
                label_suffix = " labels: " + ", ".join(f"`{label}`" for label in labels[:5])
            lines.append(
                f"- PR `#{item['pr_number']}`: `{item['pr_state']}`{label_suffix} - "
                f"{item['reason']}"
            )
    upload_transition_previews = report.reconciliation.get("upload_transition_previews", [])
    if upload_transition_previews:
        lines.extend(["", "## Upload Transition Previews", ""])
        for preview in upload_transition_previews[:20]:
            source = ""
            if preview.get("pr_number"):
                source = f" from PR `#{preview['pr_number']}`"
            elif preview.get("issue_number"):
                source = f" from issue `#{preview['issue_number']}`"
            lines.append(
                f"- `{preview['upload_id']}`{source}: `{preview['from_state']}` -> "
                f"`{preview['to_state']}` (`{preview['validation']}`) - {preview['reason']}"
            )
    feedback_decision_previews = report.reconciliation.get("feedback_decision_previews", [])
    if feedback_decision_previews:
        lines.extend(["", "## Feedback Decision Previews", ""])
        for preview in feedback_decision_previews[:20]:
            source = f"PR `#{preview['pr_number']}`" if preview.get("pr_number") else "reconciliation"
            from_decision = preview.get("from_decision") or "none"
            lines.append(
                f"- `{preview['feedback_id']}` from {source}: `{from_decision}` -> "
                f"`{preview['to_decision']}` (`{preview['validation']}`) - {preview['reason']}"
            )
    feedback_reentry_previews = report.reconciliation.get("feedback_reentry_previews", [])
    if feedback_reentry_previews:
        lines.extend(["", "## Feedback Reentry Previews", ""])
        for preview in feedback_reentry_previews[:20]:
            source = (
                f"issue `#{preview['issue_number']}`"
                if preview.get("issue_number")
                else "reconciliation"
            )
            lines.append(
                f"- `{preview['feedback_id']}` from {source}: "
                f"`{preview.get('from_decision') or 'none'}` ready "
                f"(`{preview['validation']}`) - {preview['reason']}"
            )
    referenced_evidence = _referenced_evidence(report)
    if any(referenced_evidence.values()):
        lines.extend(["", "## Referenced Evidence", ""])
        for name, values in referenced_evidence.items():
            if values:
                rendered = ", ".join(f"`{value}`" for value in values[:20])
                suffix = " ..." if len(values) > 20 else ""
                lines.append(f"- {name}: {rendered}{suffix}")
    if report.partial_failures:
        lines.extend(["", "## Partial Failures", ""])
        for failure in report.partial_failures:
            lines.append(f"- `{failure['name']}`: {failure['message']}")
    if report.input_errors:
        lines.extend(["", "## Input Errors", ""])
        for error in report.input_errors[:20]:
            location = error["path"]
            if error.get("line_number") is not None:
                location = f"{location}:{error['line_number']}"
            lines.append(f"- `{error['category']}` at `{location}`: {error['message'][:200]}")
    if report.policy_decisions:
        lines.extend(["", "## Policy Decisions", ""])
        for decision in report.policy_decisions:
            lines.append(
                f"- `{decision['action_id']}`: `{decision['status']}` - {decision['reason']}"
            )
    if report.execution_intents:
        lines.extend(["", "## Execution Intents", ""])
        for intent in report.execution_intents:
            target = intent.get("branch") or intent["target_repo"]
            metadata = _intent_metadata_summary(intent)
            suffix = f" ({metadata})" if metadata else ""
            lines.append(
                f"- `{intent['operation']}` for `{intent['action_id']}`: `{target}`{suffix}"
            )
    broker_read_requests = _broker_read_requests(report)
    if broker_read_requests:
        lines.extend(["", "## Broker Read Preflight", ""])
        for request in broker_read_requests:
            params = request.get("params", {})
            query = f" with `{params}`" if params else ""
            lines.append(
                f"- `{request['operation']}` `{request['path']}` for "
                f"`{request['target_repo']}`{query}"
            )
    if report.simulated_execution_results:
        lines.extend(["", "## Simulated Executions", ""])
        for result in report.simulated_execution_results:
            lines.append(
                f"- `{result['operation']}` for `{result['action_id']}`: `{result['status']}`"
            )
    lines.extend(["", "## Probes", ""])
    for probe in report.probes:
        lines.append(f"- `{probe.name}`: `{probe.status}` - {probe.message}")
    return "\n".join(lines) + "\n"


def _broker_read_requests(report: CuratorRunReport) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for probe in report.probes:
        if probe.name not in {
            "broker-preflight",
            "broker-upload-read-preflight",
            "broker-pr-read-preflight",
            "broker-issue-read-preflight",
        }:
            continue
        probe_requests = probe.details.get("requests")
        if not isinstance(probe_requests, list):
            continue
        for request in probe_requests:
            if isinstance(request, dict):
                requests.append(request)
    return requests


def _intent_metadata_summary(intent: dict[str, Any]) -> str:
    parts = []
    labels = intent.get("labels")
    if isinstance(labels, list) and labels:
        parts.append("labels: " + ", ".join(f"`{label}`" for label in labels[:5]))
    assignees = intent.get("assignees")
    if isinstance(assignees, list) and assignees:
        parts.append("assignees: " + ", ".join(f"`{assignee}`" for assignee in assignees[:5]))
    return "; ".join(parts)


def _upload_manifest_errors(report: CuratorRunReport) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for bundle in report.upload_bundles:
        if bundle.get("manifest_error"):
            errors.append(bundle)
    return errors[:20]


def _referenced_evidence(report: CuratorRunReport) -> dict[str, list[str]]:
    evidence: dict[str, set[str]] = {
        "uploads": set(report.referenced_upload_ids),
        "sources": set(report.referenced_source_ids),
        "sections": set(report.referenced_section_ids),
        "results": set(report.referenced_result_ids),
    }
    for action in report.proposed_actions:
        action_evidence = action.get("evidence", {})
        if not isinstance(action_evidence, dict):
            continue
        evidence["uploads"].update(_string_values(action_evidence.get("upload_ids")))
        evidence["sources"].update(_string_values(action_evidence.get("source_ids")))
        evidence["sections"].update(_string_values(action_evidence.get("section_ids")))
        evidence["results"].update(_string_values(action_evidence.get("result_ids")))
    return {name: sorted(values) for name, values in evidence.items()}


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
