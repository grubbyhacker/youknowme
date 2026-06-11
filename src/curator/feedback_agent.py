from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from curator.body import draft_action_body
from curator.models import (
    DEFAULT_PRODUCT_REPO,
    ActionEvidence,
    ExecutionIntent,
    ExecutionResult,
    ProposedAction,
)
from curator.pr_repair import (
    CODEX_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    CodexRepairError,
    _codex_base_url,
    _codex_config,
)
from curator.state import deterministic_idempotency_key
from curator.upload_agent import (
    GIT_AUTHOR_EMAIL,
    GIT_AUTHOR_NAME,
    _changed_files,
    _git_env,
    _run_git,
    _run_validation,
    _safe_name,
    _write_askpass,
)


def execute_agentic_feedback_actions(
    *,
    run_id: str,
    mode: str,
    broker_remote_url: str,
    broker_adapter,
    intents: list[ExecutionIntent],
    feedback_records: list[dict[str, object]],
    model: str,
    max_attempts: int,
    validation_command: list[str],
    output: Path,
    codex_proxy_base_url: str | None,
    codex_proxy_token: str | None,
) -> list[ExecutionResult]:
    records_by_feedback_id = {
        str(record.get("feedback_id")): record
        for record in feedback_records
        if isinstance(record.get("feedback_id"), str)
    }
    results: list[ExecutionResult] = []
    for intent in intents:
        if intent.operation == "issue.create":
            results.append(broker_adapter.create_issue(intent))
            continue
        if intent.operation != "pull.create":
            results.append(_failed_result(intent, f"unsupported feedback operation: {intent.operation}"))
            continue
        result = _execute_corpus_pr(
            run_id=run_id,
            mode=mode,
            broker_remote_url=broker_remote_url,
            broker_adapter=broker_adapter,
            intent=intent,
            feedback_records=[
                records_by_feedback_id[feedback_id]
                for feedback_id in intent.evidence.feedback_ids
                if feedback_id in records_by_feedback_id
            ],
            model=model,
            max_attempts=max_attempts,
            validation_command=validation_command,
            output=output,
            codex_proxy_base_url=codex_proxy_base_url,
            codex_proxy_token=codex_proxy_token,
        )
        if result.status == "failed" and mode == "manual_live":
            results.append(
                broker_adapter.create_issue(
                    _fallback_issue_intent(run_id=run_id, source_intent=intent, reason=result.message)
                )
            )
        else:
            results.append(result)
    return results


def _execute_corpus_pr(
    *,
    run_id: str,
    mode: str,
    broker_remote_url: str,
    broker_adapter,
    intent: ExecutionIntent,
    feedback_records: list[dict[str, object]],
    model: str,
    max_attempts: int,
    validation_command: list[str],
    output: Path,
    codex_proxy_base_url: str | None,
    codex_proxy_token: str | None,
) -> ExecutionResult:
    if not intent.branch:
        return _failed_result(intent, "feedback corpus PR intent requires a branch")
    if not broker_remote_url:
        return _failed_result(intent, "broker git remote URL is required for feedback corpus PRs")
    if not codex_proxy_base_url or not codex_proxy_token:
        return _failed_result(intent, "Codex proxy base URL and token are required for feedback PRs")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="ykm-feedback-agent-") as temp_root:
            root = Path(temp_root)
            checkout = root / "ykmcorpus"
            askpass = _write_askpass(root)
            git_env = _git_env(askpass)
            _run_git(["clone", "--depth=1", broker_remote_url, str(checkout)], cwd=None, env=git_env)
            _run_git(["checkout", "-B", intent.branch], cwd=checkout, env=git_env)
            validation: subprocess.CompletedProcess[str] | None = None
            for attempt in range(1, max_attempts + 1):
                _run_codex_feedback_agent(
                    run_id=run_id,
                    intent=intent,
                    feedback_records=feedback_records,
                    checkout=checkout,
                    output=output,
                    model=model,
                    proxy_base_url=_codex_base_url(codex_proxy_base_url),
                    proxy_token=codex_proxy_token,
                    validation_command=validation_command,
                    attempt=attempt,
                    previous_validation=validation,
                )
                changed_files = _changed_files(checkout, git_env)
                if not changed_files:
                    continue
                validation = _run_validation(checkout, validation_command)
                if validation.returncode == 0:
                    break
            changed_files = _changed_files(checkout, git_env)
            if not changed_files:
                return _failed_result(intent, "Codex feedback agent produced no corpus changes")
            if validation is None or validation.returncode != 0:
                return _failed_result(intent, "feedback corpus PR validation failed")
            _run_git(["add", "--all"], cwd=checkout, env=git_env)
            staged = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=checkout,
                env=git_env,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
            if staged.returncode == 0:
                return _failed_result(intent, "feedback agent produced no staged commit changes")
            _run_git(
                [
                    "-c",
                    f"user.name={GIT_AUTHOR_NAME}",
                    "-c",
                    f"user.email={GIT_AUTHOR_EMAIL}",
                    "commit",
                    "-m",
                    f"Curate feedback {','.join(intent.evidence.feedback_ids)[:80]}",
                ],
                cwd=checkout,
                env=git_env,
            )
            _run_git(["push", "origin", f"HEAD:refs/heads/{intent.branch}"], cwd=checkout, env=git_env)
            result = broker_adapter.create_pull(intent)
            if result.message:
                return result
            return result.model_copy(
                update={"message": f"feedback corpus PR succeeded in {time.monotonic() - started:.1f}s"}
            )
    except Exception as exc:  # noqa: BLE001 - live feedback failures must become fallback issues.
        transcript_path = str(exc.transcript_path) if isinstance(exc, CodexRepairError) else None
        suffix = f" Transcript: {transcript_path}" if transcript_path else ""
        return _failed_result(intent, f"feedback agent failed: {exc}{suffix}")


def _run_codex_feedback_agent(
    *,
    run_id: str,
    intent: ExecutionIntent,
    feedback_records: list[dict[str, object]],
    checkout: Path,
    output: Path,
    model: str,
    proxy_base_url: str,
    proxy_token: str,
    validation_command: list[str],
    attempt: int,
    previous_validation: subprocess.CompletedProcess[str] | None,
) -> Path:
    agent_dir = output / "feedback-agent" / _safe_name(intent.action_id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    transcript = agent_dir / f"codex-attempt-{attempt}.txt"
    codex_home = agent_dir / "codex-home"
    if codex_home.exists():
        shutil.rmtree(codex_home)
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        _codex_config(model=model, proxy_base_url=proxy_base_url),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        {
            "CODEX_HOME": str(codex_home),
            "OPENAI_API_KEY": proxy_token,
            "YKM_CURATOR_RUN_ID": run_id,
        }
    )
    result = subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", _feedback_agent_prompt(
            run_id=run_id,
            intent=intent,
            feedback_records=feedback_records,
            validation_command=validation_command,
            previous_validation=previous_validation,
        )],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=CODEX_TIMEOUT_SECONDS,
        check=False,
    )
    transcript.write_text(
        "STDOUT\n"
        "======\n"
        f"{result.stdout}\n\n"
        "STDERR\n"
        "======\n"
        f"{result.stderr}\n",
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise CodexRepairError(
            f"codex exec failed with exit {result.returncode}: {detail}",
            transcript_path=transcript,
        )
    return transcript


def _feedback_agent_prompt(
    *,
    run_id: str,
    intent: ExecutionIntent,
    feedback_records: list[dict[str, object]],
    validation_command: list[str],
    previous_validation: subprocess.CompletedProcess[str] | None,
) -> str:
    payload = {
        "run_id": run_id,
        "action_id": intent.action_id,
        "evidence": intent.evidence.model_dump(mode="json"),
        "feedback_records": feedback_records,
        "validation_command": validation_command,
    }
    previous = ""
    if previous_validation is not None:
        previous = (
            "\nPrevious validation failed. Fix the current checkout and rerun mentally before "
            "finishing.\n"
            f"Exit code: {previous_validation.returncode}\n"
            f"stdout tail:\n{previous_validation.stdout[-2000:]}\n"
            f"stderr tail:\n{previous_validation.stderr[-2000:]}\n"
        )
    return (
        "You are the YouKnowMe corpus feedback agent.\n"
        "You are in a clean checkout of the private ykmcorpus repository.\n"
        "Use the feedback records and durable evidence IDs to make the smallest correct corpus edit.\n"
        "Do not edit secrets, workflows, generated artifacts, or unrelated files.\n"
        "If the feedback cannot be safely resolved as a corpus change, make no changes.\n"
        f"After editing, the controller will run: {validation_command!r}.\n"
        f"{previous}\n"
        "Feedback task JSON:\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
    )


def _fallback_issue_intent(
    *,
    run_id: str,
    source_intent: ExecutionIntent,
    reason: str | None,
) -> ExecutionIntent:
    evidence = ActionEvidence.model_validate(source_intent.evidence.model_dump())
    idempotency_key = deterministic_idempotency_key("product_issue", evidence)
    action = ProposedAction(
        action_id=source_intent.action_id,
        action_type="product_issue",
        classification="fallback",
        idempotency_key=idempotency_key,
        evidence=evidence,
        target_repo=DEFAULT_PRODUCT_REPO,
    )
    body = draft_action_body(run_id, action)
    if reason:
        body = f"{body}\n\n## Fallback Reason\n\n{reason[:1000]}\n"
    return ExecutionIntent(
        action_id=action.action_id,
        operation="issue.create",
        idempotency_key=action.idempotency_key,
        target_repo=DEFAULT_PRODUCT_REPO,
        evidence=evidence,
        title=f"YouKnowMe Curator fallback: {', '.join(evidence.feedback_ids[:3])}",
        body=body,
        labels=["ykm-curator", "feedback", "needs-triage"],
        assignees=["grubbyhacker"],
    )


def _failed_result(intent: ExecutionIntent, message: str) -> ExecutionResult:
    return ExecutionResult(
        action_id=intent.action_id,
        operation=intent.operation,
        idempotency_key=intent.idempotency_key,
        status="failed",
        target_repo=intent.target_repo,
        branch=intent.branch,
        message=message,
    )
