from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from curator.file_policy import is_backup_or_temp_path
from curator.models import (
    CuratorPrReconciliation,
    CuratorPrSnapshot,
    PrRepairExecutor,
    PrRepairResult,
)
from curator.upload_pr import GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME


GIT_TIMEOUT_SECONDS = 120
CODEX_TIMEOUT_SECONDS = 900
VALIDATION_TIMEOUT_SECONDS = 240
REPAIRABLE_PR_STATES = {
    "changes_requested",
    "commented_needs_triage",
    "checks_failed",
    "checks_missing",
}


class CodexRepairError(RuntimeError):
    def __init__(self, message: str, *, transcript_path: Path) -> None:
        super().__init__(message)
        self.transcript_path = transcript_path


def execute_pr_repairs(
    *,
    run_id: str,
    mode: str,
    reconciliations: list[CuratorPrReconciliation],
    snapshots: list[CuratorPrSnapshot],
    executor: PrRepairExecutor,
    model: str,
    validation_command: list[str],
    max_repairs: int,
    output: Path,
    broker_remote_url: str | None,
    codex_proxy_base_url: str | None,
    codex_proxy_token: str | None,
) -> list[PrRepairResult]:
    if max_repairs <= 0:
        return []
    snapshots_by_number = {snapshot.number: snapshot for snapshot in snapshots}
    results: list[PrRepairResult] = []
    for reconciliation in reconciliations:
        if len(results) >= max_repairs:
            break
        if reconciliation.pr_state not in REPAIRABLE_PR_STATES:
            continue
        snapshot = snapshots_by_number.get(reconciliation.pr_number)
        results.append(
            _execute_one_repair(
                run_id=run_id,
                mode=mode,
                reconciliation=reconciliation,
                snapshot=snapshot,
                executor=executor,
                model=model,
                validation_command=validation_command,
                output=output,
                broker_remote_url=broker_remote_url,
                codex_proxy_base_url=codex_proxy_base_url,
                codex_proxy_token=codex_proxy_token,
            )
        )
    return results


def _execute_one_repair(
    *,
    run_id: str,
    mode: str,
    reconciliation: CuratorPrReconciliation,
    snapshot: CuratorPrSnapshot | None,
    executor: PrRepairExecutor,
    model: str,
    validation_command: list[str],
    output: Path,
    broker_remote_url: str | None,
    codex_proxy_base_url: str | None,
    codex_proxy_token: str | None,
) -> PrRepairResult:
    branch = reconciliation.branch
    if not branch or not branch.startswith("curator/"):
        return _repair_result(
            reconciliation,
            executor=executor,
            model=model,
            status="rejected",
            message="PR repair requires a Curator-owned branch.",
            validation_command=validation_command,
        )
    if executor == "fixture":
        return _fixture_result(reconciliation, model=model, validation_command=validation_command)
    if not broker_remote_url:
        return _repair_result(
            reconciliation,
            executor=executor,
            model=model,
            status="executor_failed",
            message="broker git remote URL is required for Codex PR repair.",
            validation_command=validation_command,
        )
    if not codex_proxy_base_url or not codex_proxy_token:
        return _repair_result(
            reconciliation,
            executor=executor,
            model=model,
            status="executor_failed",
            message="Codex proxy base URL and token are required for Codex PR repair.",
            validation_command=validation_command,
        )
    try:
        with tempfile.TemporaryDirectory(prefix="ykm-pr-repair-") as temp_root:
            root = Path(temp_root)
            checkout = root / "ykmcorpus"
            askpass = _write_askpass(root)
            git_env = _git_env(askpass)
            _run_git(["clone", "--branch", branch, "--depth=1", broker_remote_url, str(checkout)], cwd=None, env=git_env)
            transcript_path = _run_codex(
                run_id=run_id,
                reconciliation=reconciliation,
                snapshot=snapshot,
                checkout=checkout,
                output=output,
                model=model,
                proxy_base_url=_codex_base_url(codex_proxy_base_url),
                proxy_token=codex_proxy_token,
            )
            changed_files = _changed_files(checkout, git_env)
            if not changed_files:
                return _repair_result(
                    reconciliation,
                    executor=executor,
                    model=model,
                    status="executor_failed",
                    message="Codex PR repair produced no checkout changes.",
                    validation_command=validation_command,
                    transcript_path=str(transcript_path),
                )
            if _has_forbidden_changed_file(changed_files):
                return _repair_result(
                    reconciliation,
                    executor=executor,
                    model=model,
                    status="rejected",
                    message="Codex PR repair changed forbidden private runtime files.",
                    changed_files=changed_files,
                    validation_command=validation_command,
                    transcript_path=str(transcript_path),
                )
            if _has_workflow_changed_file(changed_files):
                diff_stat = _git_output(["diff", "--stat"], cwd=checkout, env=git_env)
                return _repair_result(
                    reconciliation,
                    executor=executor,
                    model=model,
                    status="rejected",
                    message=(
                        "Codex PR repair changed GitHub workflow files, but the Curator "
                        "GitHub App is not granted workflow write permission."
                    ),
                    changed_files=changed_files,
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    transcript_path=str(transcript_path),
                )
            validation = _run_validation(checkout, validation_command)
            diff_stat = _git_output(["diff", "--stat"], cwd=checkout, env=git_env)
            if validation.returncode != 0:
                return _repair_result(
                    reconciliation,
                    executor=executor,
                    model=model,
                    status="validation_failed",
                    message="PR repair validation failed.",
                    changed_files=changed_files,
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    validation_returncode=validation.returncode,
                    validation_stdout_tail=_tail(validation.stdout),
                    validation_stderr_tail=_tail(validation.stderr),
                    transcript_path=str(transcript_path),
                )
            if mode != "manual_live":
                return _repair_result(
                    reconciliation,
                    executor=executor,
                    model=model,
                    status="validated",
                    message="PR repair validated in observe mode; branch was not pushed.",
                    changed_files=changed_files,
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    validation_returncode=validation.returncode,
                    validation_stdout_tail=_tail(validation.stdout),
                    validation_stderr_tail=_tail(validation.stderr),
                    transcript_path=str(transcript_path),
                    review_request_comment=_review_request_comment(
                        reconciliation,
                        changed_files=changed_files,
                    ),
                )
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
                return _repair_result(
                    reconciliation,
                    executor=executor,
                    model=model,
                    status="executor_failed",
                    message="PR repair produced no staged commit changes.",
                    changed_files=changed_files,
                    validation_command=validation_command,
                    transcript_path=str(transcript_path),
                )
            _run_git(
                [
                    "-c",
                    f"user.name={GIT_AUTHOR_NAME}",
                    "-c",
                    f"user.email={GIT_AUTHOR_EMAIL}",
                    "commit",
                    "-m",
                    f"Repair Curator PR #{reconciliation.pr_number}",
                ],
                cwd=checkout,
                env=git_env,
            )
            repair_head_sha = _git_output(["rev-parse", "HEAD"], cwd=checkout, env=git_env).strip()
            _run_git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=checkout, env=git_env)
            return _repair_result(
                reconciliation,
                executor=executor,
                model=model,
                status="pushed",
                message="PR repair validated and pushed to the existing Curator branch.",
                changed_files=changed_files,
                repair_head_sha=repair_head_sha,
                diff_stat=diff_stat,
                validation_command=validation_command,
                validation_returncode=validation.returncode,
                validation_stdout_tail=_tail(validation.stdout),
                validation_stderr_tail=_tail(validation.stderr),
                transcript_path=str(transcript_path),
                review_request_comment=_review_request_comment(
                    reconciliation,
                    changed_files=changed_files,
                ),
                review_request_comment_status="pending",
                pushed=True,
            )
    except Exception as exc:  # noqa: BLE001 - live repair failures must be reportable.
        transcript_path = (
            str(exc.transcript_path) if isinstance(exc, CodexRepairError) else None
        )
        return _repair_result(
            reconciliation,
            executor=executor,
            model=model,
            status="push_failed" if mode == "manual_live" else "executor_failed",
            message=f"PR repair failed: {exc}",
            validation_command=validation_command,
            transcript_path=transcript_path,
        )


def _fixture_result(
    reconciliation: CuratorPrReconciliation,
    *,
    model: str,
    validation_command: list[str],
) -> PrRepairResult:
    return _repair_result(
        reconciliation,
        executor="fixture",
        model=model,
        status="validated",
        message="Fixture PR repair validated without live checkout mutation.",
        changed_files=[".ykm/corpus-policy.yaml"],
        diff_stat="fixture repair\n",
        validation_command=validation_command,
        validation_returncode=0,
        review_request_comment=_review_request_comment(
            reconciliation,
            changed_files=[".ykm/corpus-policy.yaml"],
        ),
    )


def _run_codex(
    *,
    run_id: str,
    reconciliation: CuratorPrReconciliation,
    snapshot: CuratorPrSnapshot | None,
    checkout: Path,
    output: Path,
    model: str,
    proxy_base_url: str,
    proxy_token: str,
) -> Path:
    repair_dir = output / "pr-repair" / f"pr-{reconciliation.pr_number}"
    repair_dir.mkdir(parents=True, exist_ok=True)
    transcript = repair_dir / "codex-transcript.txt"
    codex_home = repair_dir / "codex-home"
    if codex_home.exists():
        shutil.rmtree(codex_home)
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        _codex_config(model=model, proxy_base_url=proxy_base_url),
        encoding="utf-8",
    )
    prompt = _repair_prompt(reconciliation, snapshot)
    env = dict(os.environ)
    env.update(
        {
            "CODEX_HOME": str(codex_home),
            "OPENAI_API_KEY": proxy_token,
            "YKM_CURATOR_RUN_ID": run_id,
        }
    )
    result = subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", prompt],
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


def _codex_config(*, model: str, proxy_base_url: str) -> str:
    return (
        f'model = "{model}"\n'
        'model_provider = "ykm_proxy"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n\n'
        "[model_providers.ykm_proxy]\n"
        'name = "YKM Codex Proxy"\n'
        f'base_url = "{proxy_base_url}"\n'
        'env_key = "OPENAI_API_KEY"\n'
        'wire_api = "responses"\n'
        'env_http_headers = { "X-GH-Agent-Run-ID" = "YKM_CURATOR_RUN_ID" }\n'
    )


def _repair_prompt(
    reconciliation: CuratorPrReconciliation,
    snapshot: CuratorPrSnapshot | None,
) -> str:
    comments = "\n\n".join(_review_evidence(snapshot))
    title = snapshot.title if snapshot and snapshot.title else f"PR #{reconciliation.pr_number}"
    return (
        "You are repairing a YouKnowMe Curator pull request in this checkout.\n"
        "Make the minimum repo changes needed to satisfy the review and validation. "
        "Do not merge, close, relabel, or use network services directly. "
        "Do not commit; leave changes in the working tree for the Curator to validate. "
        "Do not edit `.github/workflows/*`; the Curator GitHub App cannot push workflow "
        "changes with its current permissions. "
        "Avoid broad exploration. If the review evidence names concrete files, fields, "
        "or path filters, edit those files directly before running validation.\n\n"
        f"PR: #{reconciliation.pr_number} {title}\n"
        f"Branch: {reconciliation.branch}\n"
        f"State: {reconciliation.pr_state}\n"
        f"Reason: {reconciliation.reason}\n"
        f"Labels: {', '.join(reconciliation.labels) or 'none'}\n"
        f"Uploads: {', '.join(reconciliation.upload_ids) or 'none'}\n"
        f"Feedback IDs: {', '.join(reconciliation.feedback_ids) or 'none'}\n\n"
        "Review and comment evidence:\n"
        f"{comments or '(no review comment text was available)'}\n\n"
        "Success criteria: the corpus validation command must pass after your edits.\n\n"
        "If this PR is the known skipped-validation shape involving "
        "`preferences/dev-environment.md`, repair it directly: add `preferences` to "
        "`.ykm/corpus-policy.yaml` `corpus_roots`, resolve the disallowed `tools` tag by "
        "using an existing allowed tag when possible, and resolve "
        "`related: [ykm-mcp-server]` by adding an appropriate `external_related_ids` entry "
        "or otherwise making the reference valid. The long-term workflow path-filter fix "
        "must be handled separately because this repair worker cannot push workflow files."
    )


def _review_evidence(snapshot: CuratorPrSnapshot | None) -> list[str]:
    if snapshot is None:
        return []
    evidence: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = " ".join(value.split())
        if not text or text in seen:
            return
        seen.add(text)
        evidence.append(text[:2000])

    for comment in snapshot.review_comments:
        add(comment)
    for review in snapshot.reviews:
        if review.body:
            prefix = f"Review {review.state}"
            if review.author_login:
                prefix += f" by {review.author_login}"
            add(f"{prefix}: {review.body}")
    for thread in snapshot.review_threads:
        location = thread.path or "unknown file"
        if thread.line is not None:
            location += f":{thread.line}"
        for comment in thread.comments:
            if not comment.body:
                continue
            prefix = f"Inline review on {location}"
            if comment.author_login:
                prefix += f" by {comment.author_login}"
            add(f"{prefix}: {comment.body}")
    return evidence[:20]


def _run_validation(checkout: Path, validation_command: list[str]) -> subprocess.CompletedProcess[str]:
    if (checkout / "mise.toml").exists():
        subprocess.run(
            ["mise", "trust", "--yes", str(checkout / "mise.toml")],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=VALIDATION_TIMEOUT_SECONDS,
            check=False,
        )
    return subprocess.run(
        validation_command,
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=VALIDATION_TIMEOUT_SECONDS,
        check=False,
    )


def _changed_files(checkout: Path, env: dict[str, str]) -> list[str]:
    output = _git_output(["status", "--porcelain"], cwd=checkout, env=env)
    files: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[-1]
        files.append(path)
    return sorted(set(files))


def _has_forbidden_changed_file(paths: list[str]) -> bool:
    return any(
        path == ".env" or path.startswith(".env.") or is_backup_or_temp_path(path)
        for path in paths
    )


def _has_workflow_changed_file(paths: list[str]) -> bool:
    return any(path.startswith(".github/workflows/") for path in paths)


def _run_git(args: list[str], *, cwd: Path | None, env: dict[str, str]) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise RuntimeError(f"git {' '.join(args[:2])} failed with exit {result.returncode}: {detail}")


def _git_output(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise RuntimeError(f"git {' '.join(args[:2])} failed with exit {result.returncode}: {detail}")
    return result.stdout


def _write_askpass(temp_root: Path) -> Path:
    askpass = temp_root / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "*Username*) printf '%s\\n' \"$BROKER_AGENT_ID\" ;;\n"
        "*) printf '%s\\n' \"$BROKER_AGENT_SECRET\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    return askpass


def _git_env(askpass: Path) -> dict[str, str]:
    env = dict(os.environ)
    if not env.get("BROKER_AGENT_ID") or not env.get("BROKER_AGENT_SECRET"):
        raise ValueError("BROKER_AGENT_ID and BROKER_AGENT_SECRET are required for broker git push")
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _codex_base_url(raw: str) -> str:
    base = raw.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _tail(value: str, limit: int = 2000) -> str:
    return value[-limit:] if len(value) > limit else value


def _review_request_comment(
    reconciliation: CuratorPrReconciliation,
    *,
    changed_files: list[str],
) -> str:
    changed_list = "\n".join(f"- `{path}`" for path in changed_files) or "- No files listed."
    run_id = reconciliation.run_id or "unknown"
    return (
        "Curator repair completed and this PR is ready for review again.\n\n"
        "What I fixed:\n"
        f"{changed_list}\n\n"
        "Why it broke:\n"
        "- The initial PR did not fully account for the validation and policy impact of the "
        "requested corpus changes, so the owner review found issues that had to be repaired on the "
        "Curator branch.\n\n"
        "How it should not break again:\n"
        "- The Curator repair path validates the repaired branch before pushing, treats failed or "
        "missing validation as actionable, and posts this handoff comment so the PR returns to "
        "owner review instead of relying only on labels.\n\n"
        f"Please review PR #{reconciliation.pr_number} again when ready.\n\n"
        "## Curator Markers\n\n"
        f"YKM-Curator-Run: {run_id}\n"
        "YKM-Curator-Action: repair\n"
        "YKM-Curator-Action-Type: pr_repair\n"
        f"YKM-Curator-PR: {reconciliation.pr_number}\n"
    )


def _repair_result(
    reconciliation: CuratorPrReconciliation,
    *,
    executor: PrRepairExecutor,
    model: str,
    status: str,
    message: str,
    changed_files: list[str] | None = None,
    repair_head_sha: str | None = None,
    diff_stat: str | None = None,
    validation_command: list[str],
    validation_returncode: int | None = None,
    validation_stdout_tail: str = "",
    validation_stderr_tail: str = "",
    transcript_path: str | None = None,
    review_request_comment: str | None = None,
    review_request_comment_status: str = "not_applicable",
    review_request_comment_message: str | None = None,
    pushed: bool = False,
) -> PrRepairResult:
    return PrRepairResult(
        pr_number=reconciliation.pr_number,
        branch=reconciliation.branch,
        pr_state=reconciliation.pr_state,
        executor=executor,
        model=model,
        status=status,
        message=message,
        changed_files=changed_files or [],
        repair_head_sha=repair_head_sha,
        diff_stat=diff_stat,
        validation_command=validation_command,
        validation_returncode=validation_returncode,
        validation_stdout_tail=validation_stdout_tail,
        validation_stderr_tail=validation_stderr_tail,
        transcript_path=transcript_path,
        review_request_comment=review_request_comment,
        review_request_comment_status=review_request_comment_status,
        review_request_comment_message=review_request_comment_message,
        pushed=pushed,
    )
