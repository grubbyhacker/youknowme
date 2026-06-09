from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from pydantic import ValidationError

from curator.models import CuratorRunReport
from curator.runner import CuratorDryRunConfig, parse_curator_task_payload, run_curator_dry_run


def main() -> None:
    parser = argparse.ArgumentParser(prog="curator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--run-id", default=os.getenv("SANDBOX_RUN_ID", "local-curator-run"))
    run.add_argument("--intake", type=Path, default=Path(os.getenv("YKM_INTAKE_PATH", "/data/intake")))
    run.add_argument("--logs", type=Path, default=Path(os.getenv("YKM_LOG_DIR", "/data/logs")))
    run.add_argument("--output", type=Path, default=Path(os.getenv("YKM_CURATOR_OUTPUT", "/output")))
    run.add_argument("--task", type=Path, default=Path("/input/task.json"))
    run.add_argument("--no-task", action="store_true")
    run.add_argument("--broker-url", default=os.getenv("BROKER_URL"))
    run.add_argument("--model-proxy-url", default=os.getenv("GH_AGENT_PROXY_URL"))
    run.add_argument("--model-proxy-token", default=os.getenv("GH_AGENT_PROXY_TOKEN"))
    run.add_argument("--broker-fixture", type=Path)
    run.add_argument("--model-proxy-fixture", type=Path)
    run.add_argument("--require-broker", action="store_true")
    run.add_argument("--require-model-proxy", action="store_true")
    run.add_argument("--lock-path", type=Path)
    run.add_argument("--recover-stale-lock", action="store_true")
    run.add_argument("--simulate-execution", action="store_true")
    run.add_argument("--enable-broker-reads", action="store_true")

    inspect_task = subparsers.add_parser("inspect-task")
    inspect_task.add_argument("task", type=Path)

    inspect_report = subparsers.add_parser("inspect-report")
    inspect_report.add_argument("report", type=Path)

    args = parser.parse_args()
    if args.command == "run":
        report = run_curator(
            CuratorDryRunConfig(
                run_id=args.run_id,
                intake=args.intake,
                logs=args.logs,
                output=args.output,
                task=None if args.no_task else args.task,
                broker_url=args.broker_url,
                model_proxy_url=args.model_proxy_url,
                model_proxy_token=args.model_proxy_token,
                broker_fixture=args.broker_fixture,
                model_proxy_fixture=args.model_proxy_fixture,
                required_broker=args.require_broker,
                required_model_proxy=args.require_model_proxy,
                lock_path=args.lock_path,
                recover_stale_lock=args.recover_stale_lock,
                simulate_execution=args.simulate_execution,
                enable_broker_reads=args.enable_broker_reads,
            )
        )
        print(report.model_dump_json(indent=2))
        if report.status != "pass":
            raise SystemExit(1)
    elif args.command == "inspect-task":
        try:
            payload = json.loads(args.task.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("task JSON must be an object")
            _, task, message = parse_curator_task_payload(payload)
            if task is None:
                raise ValueError(message)
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise SystemExit(f"invalid Curator task: {exc}") from exc
        print(task.model_dump_json(indent=2))
    elif args.command == "inspect-report":
        try:
            report = CuratorRunReport.model_validate_json(
                args.report.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise SystemExit(f"invalid Curator report: {exc}") from exc
        print(json.dumps(_report_summary(report), indent=2, sort_keys=True))


def run_curator(config: CuratorDryRunConfig):
    return run_curator_dry_run(config)


def _report_summary(report: CuratorRunReport) -> dict[str, object]:
    return {
        "run_id": report.run_id,
        "mode": report.mode,
        "status": report.status,
        "enabled_actions": report.enabled_actions,
        "checkpoint_advanced": report.checkpoint_advanced,
        "feedback_checkpoint": report.feedback_checkpoint,
        "feedback_count": report.feedback_count,
        "included_feedback_count": len(report.included_feedback_ids),
        "validation_failure_count": report.validation_failure_count,
        "input_error_count": report.input_error_count,
        "upload_queue_counts": report.upload_queue_counts,
        "included_upload_count": len(report.included_upload_ids),
        "referenced_upload_count": len(report.referenced_upload_ids),
        "referenced_source_count": len(report.referenced_source_ids),
        "referenced_section_count": len(report.referenced_section_ids),
        "referenced_result_count": len(report.referenced_result_ids),
        "proposed_action_count": report.proposed_action_count,
        "upload_proposed_action_count": report.upload_proposed_action_count,
        "upload_review_preview_count": report.upload_review_preview_count,
        "policy_denial_count": report.policy_denial_count,
        "execution_intent_count": report.execution_intent_count,
        "pr_reconciliation_count": int(
            report.reconciliation.get("pr_reconciliation_count", 0)
        ),
        "pr_state_counts": report.reconciliation.get("pr_state_counts", {}),
        "simulated_execution_count": report.simulated_execution_count,
        "github_mutation_count": report.github_mutation_count,
        "capacity_deferral_count": report.capacity_deferral_count,
        "capacity_deferred_feedback_count": len(report.capacity_deferred_feedback_ids),
        "partial_failure_count": len(report.partial_failures),
        "partial_failure_names": sorted(
            {
                failure.get("name")
                for failure in report.partial_failures
                if isinstance(failure.get("name"), str)
            }
        ),
        "model_call_count": report.model_call_count,
        "model_token_count": report.model_token_count,
        "model_budget_exhausted": report.model_budget_exhausted,
    }


if __name__ == "__main__":
    main()
