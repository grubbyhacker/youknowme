from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from curator.models import ExecutionIntent, ExecutionResult, UploadBundleSnapshot, UploadReviewPreview
from curator.file_policy import forbidden_backup_or_temp_paths
from curator.pr_repair import (
    CODEX_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    VALIDATION_TIMEOUT_SECONDS,
    CodexRepairError,
    _codex_base_url,
    _codex_config,
    _tail,
)
from curator.upload_observe import UploadReviewObservation
from curator.upload_pr import (
    GIT_AUTHOR_EMAIL,
    GIT_AUTHOR_NAME,
    upload_review_pull_intent,
)


MAX_UPLOAD_AGENT_FILES = 5
MAX_UPLOAD_AGENT_FILE_CHARS = 20000
MAX_UPLOAD_AGENT_MANIFEST_CHARS = 20000
CURATOR_GUIDANCE_PATH = Path("skills/curator-guidance.md")
FORBIDDEN_CHANGED_FILES = {".env"}


def execute_agentic_upload_review_prs(
    *,
    run_id: str,
    mode: str,
    broker_remote_url: str,
    broker_adapter,
    previews: list[UploadReviewPreview],
    bundles: list[UploadBundleSnapshot],
    model: str,
    max_attempts: int,
    validation_command: list[str],
    output: Path,
    codex_proxy_base_url: str | None,
    codex_proxy_token: str | None,
    on_branch_pushed: Callable[[ExecutionIntent], None] | None = None,
) -> tuple[list[ExecutionResult], list[UploadReviewObservation]]:
    results: list[ExecutionResult] = []
    observations: list[UploadReviewObservation] = []
    bundles_by_upload = {bundle.upload_id: bundle for bundle in bundles}
    for preview in previews:
        bundle = bundles_by_upload.get(preview.upload_id)
        result, observation = _execute_one_agentic_upload_review(
            run_id=run_id,
            mode=mode,
            broker_remote_url=broker_remote_url,
            broker_adapter=broker_adapter,
            preview=preview,
            bundle=bundle,
            model=model,
            max_attempts=max_attempts,
            validation_command=validation_command,
            output=output,
            codex_proxy_base_url=codex_proxy_base_url,
            codex_proxy_token=codex_proxy_token,
            on_branch_pushed=on_branch_pushed,
        )
        observations.append(observation)
        if result is not None:
            results.append(result)
    return results, observations


def _execute_one_agentic_upload_review(
    *,
    run_id: str,
    mode: str,
    broker_remote_url: str,
    broker_adapter,
    preview: UploadReviewPreview,
    bundle: UploadBundleSnapshot | None,
    model: str,
    max_attempts: int,
    validation_command: list[str],
    output: Path,
    codex_proxy_base_url: str | None,
    codex_proxy_token: str | None,
    on_branch_pushed: Callable[[ExecutionIntent], None] | None,
) -> tuple[ExecutionResult | None, UploadReviewObservation]:
    intent = upload_review_pull_intent(run_id=run_id, preview=preview)
    if bundle is None:
        return None, _agent_observation(
            preview,
            status="fail",
            message="upload review bundle disappeared before agentic review",
            model=model,
            validation_command=validation_command,
        )
    if not broker_remote_url:
        return _failed_result(intent, "broker git remote URL is required for upload PR creation"), _agent_observation(
            preview,
            status="fail",
            message="broker git remote URL is required for upload PR creation",
            model=model,
            validation_command=validation_command,
        )
    if not codex_proxy_base_url or not codex_proxy_token:
        return _failed_result(intent, "Codex proxy base URL and token are required for upload review"), _agent_observation(
            preview,
            status="fail",
            message="Codex proxy base URL and token are required for upload review",
            model=model,
            validation_command=validation_command,
        )
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="ykm-upload-agent-") as temp_root:
            root = Path(temp_root)
            checkout = root / "ykmcorpus"
            askpass = _write_askpass(root)
            git_env = _git_env(askpass)
            _run_git(["clone", "--depth=1", broker_remote_url, str(checkout)], cwd=None, env=git_env)
            _run_git(["checkout", "-B", preview.branch], cwd=checkout, env=git_env)
            base_ref = _git_output(["rev-parse", "HEAD"], cwd=checkout, env=git_env).strip()
            transcript_path: Path | None = None
            summary_path = output / "upload-review-agent" / _safe_name(preview.upload_id) / "summary.json"
            validation: subprocess.CompletedProcess[str] | None = None
            attempts_used = 0
            for attempt in range(1, max_attempts + 1):
                attempts_used = attempt
                transcript_path = _run_codex_upload_agent(
                    run_id=run_id,
                    preview=preview,
                    bundle=bundle,
                    checkout=checkout,
                    output=output,
                    summary_path=summary_path,
                    model=model,
                    proxy_base_url=_codex_base_url(codex_proxy_base_url),
                    proxy_token=codex_proxy_token,
                    validation_command=validation_command,
                    attempt=attempt,
                    previous_validation=validation,
                )
                changed_files = _branch_changed_files(checkout, git_env, base_ref)
                if not changed_files:
                    continue
                forbidden_message = _forbidden_change_message(changed_files)
                if forbidden_message is not None:
                    return None, _agent_observation(
                        preview,
                        status="fail",
                        message=forbidden_message,
                        model=model,
                        attempts=attempt,
                        changed_files=changed_files,
                        validation_command=validation_command,
                        transcript_path=transcript_path,
                        elapsed_seconds=time.monotonic() - started,
                    )
                validation = _run_validation(checkout, validation_command)
                if validation.returncode == 0:
                    break
            changed_files = _branch_changed_files(checkout, git_env, base_ref)
            diff_stat = _diff_stat(checkout, git_env, changed_files, base_ref=base_ref)
            summary = _read_agent_summary(summary_path, changed_files)
            draft_paths = _final_draft_paths(summary, changed_files)
            if not changed_files:
                return None, _agent_observation(
                    preview,
                    status="fail",
                    message="Codex upload review produced no checkout changes.",
                    model=model,
                    attempts=attempts_used,
                    changed_files=[],
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    transcript_path=transcript_path,
                    elapsed_seconds=time.monotonic() - started,
                )
            if validation is None or validation.returncode != 0:
                return None, _agent_observation(
                    preview,
                    status="fail",
                    message="upload review validation failed after Codex agent edits.",
                    model=model,
                    attempts=attempts_used,
                    draft_paths=draft_paths,
                    changed_files=changed_files,
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    validation=validation,
                    transcript_path=transcript_path,
                    elapsed_seconds=time.monotonic() - started,
                )
            validated_intent = upload_review_pull_intent(
                run_id=run_id,
                preview=preview,
                content_summary=summary.get("content_summary"),
                draft_paths=draft_paths,
            )
            if mode != "manual_live":
                observation = _agent_observation(
                    preview,
                    status="pass",
                    message="Codex upload review produced a validated corpus diff.",
                    model=model,
                    attempts=attempts_used,
                    draft_paths=draft_paths,
                    changed_files=changed_files,
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    validation=validation,
                    transcript_path=transcript_path,
                    elapsed_seconds=time.monotonic() - started,
                )
                return None, observation
            uncommitted_files = _changed_files(checkout, git_env)
            agent_committed = _has_branch_commits(checkout, git_env, base_ref)
            if uncommitted_files:
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
                    return _failed_result(validated_intent, "upload review agent produced no staged commit changes"), _agent_observation(
                        preview,
                        status="fail",
                        message="upload review agent produced no staged commit changes",
                        model=model,
                        attempts=attempts_used,
                        draft_paths=draft_paths,
                        changed_files=changed_files,
                        diff_stat=diff_stat,
                        validation_command=validation_command,
                        validation=validation,
                        transcript_path=transcript_path,
                        elapsed_seconds=time.monotonic() - started,
                    )
                _run_git(
                    [
                        "-c",
                        f"user.name={GIT_AUTHOR_NAME}",
                        "-c",
                        f"user.email={GIT_AUTHOR_EMAIL}",
                        "commit",
                        "-m",
                        f"Curate upload {preview.upload_id}",
                    ],
                    cwd=checkout,
                    env=git_env,
                )
            elif not agent_committed:
                return _failed_result(validated_intent, "upload review agent produced no commit changes"), _agent_observation(
                    preview,
                    status="fail",
                    message="upload review agent produced no commit changes",
                    model=model,
                    attempts=attempts_used,
                    draft_paths=draft_paths,
                    changed_files=changed_files,
                    diff_stat=diff_stat,
                    validation_command=validation_command,
                    validation=validation,
                    transcript_path=transcript_path,
                    elapsed_seconds=time.monotonic() - started,
                )
            observation = _agent_observation(
                preview,
                status="pass",
                message="Codex upload review produced a validated corpus branch delta.",
                model=model,
                attempts=attempts_used,
                draft_paths=draft_paths,
                changed_files=changed_files,
                diff_stat=diff_stat,
                validation_command=validation_command,
                validation=validation,
                transcript_path=transcript_path,
                elapsed_seconds=time.monotonic() - started,
            )
            _run_git(["push", "origin", f"HEAD:refs/heads/{preview.branch}"], cwd=checkout, env=git_env)
            if on_branch_pushed is not None:
                on_branch_pushed(validated_intent)
            result = broker_adapter.create_pull(validated_intent)
            return result, observation
    except Exception as exc:  # noqa: BLE001 - live upload failures must be reportable.
        transcript_path = str(exc.transcript_path) if isinstance(exc, CodexRepairError) else None
        return _failed_result(intent, f"upload review agent failed: {exc}"), _agent_observation(
            preview,
            status="fail",
            message=f"upload review agent failed: {exc}",
            model=model,
            attempts=max_attempts,
            validation_command=validation_command,
            transcript_path=Path(transcript_path) if transcript_path else None,
            elapsed_seconds=time.monotonic() - started,
        )


def _run_codex_upload_agent(
    *,
    run_id: str,
    preview: UploadReviewPreview,
    bundle: UploadBundleSnapshot,
    checkout: Path,
    output: Path,
    summary_path: Path,
    model: str,
    proxy_base_url: str,
    proxy_token: str,
    validation_command: list[str],
    attempt: int,
    previous_validation: subprocess.CompletedProcess[str] | None,
) -> Path:
    agent_dir = output / "upload-review-agent" / _safe_name(preview.upload_id)
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
    prompt = _upload_agent_prompt(
        run_id=run_id,
        preview=preview,
        bundle=bundle,
        checkout=checkout,
        validation_command=validation_command,
        summary_path=summary_path,
        previous_validation=previous_validation,
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


def _upload_agent_prompt(
    *,
    run_id: str,
    preview: UploadReviewPreview,
    bundle: UploadBundleSnapshot,
    checkout: Path | None = None,
    validation_command: list[str],
    summary_path: Path,
    previous_validation: subprocess.CompletedProcess[str] | None,
) -> str:
    payload = {
        "run_id": run_id,
        "upload_id": bundle.upload_id,
        "preview": preview.model_dump(mode="json"),
        "manifest": _read_upload_manifest(Path(bundle.path)),
        "files": _read_upload_files(Path(bundle.path)),
        "curator_guidance": _read_curator_guidance(checkout),
        "validation_command": validation_command,
        "summary_path": str(summary_path),
    }
    previous = ""
    if previous_validation is not None:
        previous = (
            "\nPrevious validation failed:\n"
            f"exit: {previous_validation.returncode}\n"
            f"stdout tail:\n{_tail(previous_validation.stdout)}\n"
            f"stderr tail:\n{_tail(previous_validation.stderr)}\n"
        )
    return (
        "You are the YouKnowMe Curator upload agent working in a ykmcorpus checkout.\n"
        "Normalize the supplied upload into a small reviewable corpus diff. Edit markdown corpus "
        "files and `.ykm/corpus-policy.yaml` directly when bounded policy vocabulary changes are "
        "needed. The corpus policy is a consistency guardrail and review surface, not an immutable "
        "permission boundary.\n\n"
        "You may make local commits on the prepared branch if that is the natural way to complete "
        "the work. Do not push, merge, close issues, relabel, open pull requests, or use network "
        "services directly. "
        "Do not edit `.github/workflows/*`, `.env`, `.env.*`, or generated private runtime files. "
        "Never create backup, temporary, swap, `.bak`, `.orig`, `.rej`, or `~` files in git. "
        "Do not copy secrets into the corpus. You may run tests and validation commands in this "
        "checkout and should fix validation failures before finishing.\n\n"
        "Follow any approved curator_guidance in the upload payload. Guidance is owner-reviewed "
        "source policy, not optional advice.\n\n"
        "When finished, write a JSON object to the exact summary_path with this shape: "
        '{"content_summary":"one short sentence identifying the uploaded document",'
        '"draft_paths":["final/markdown-path.md"]}. The content_summary must help the owner '
        "recognize the uploaded document at a glance without quoting intake excerpts.\n\n"
        "Success criteria: the validation command must pass after your edits. Leave either "
        "uncommitted working-tree changes or local commits on the prepared branch. Curator will "
        "validate the final branch delta, push, and open the PR.\n"
        f"{previous}\n"
        "Upload payload JSON:\n"
        f"{json.dumps(payload, sort_keys=True)}"
    )


def _read_curator_guidance(checkout: Path | None) -> str:
    if checkout is None:
        return ""
    path = checkout / CURATOR_GUIDANCE_PATH
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return text[:8000]


def _read_upload_manifest(bundle_path: Path) -> dict[str, Any]:
    path = bundle_path / "manifest.json"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if len(text) > MAX_UPLOAD_AGENT_MANIFEST_CHARS:
        raise ValueError("upload manifest is too large for agentic upload review")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("upload manifest must be a JSON object")
    return payload


def _read_upload_files(bundle_path: Path) -> list[dict[str, str]]:
    files_dir = bundle_path / "files"
    if not files_dir.exists() or not files_dir.is_dir():
        raise ValueError("upload bundle has no files directory for agentic upload review")
    paths = sorted(path for path in files_dir.iterdir() if path.is_file())
    if len(paths) > MAX_UPLOAD_AGENT_FILES:
        raise ValueError(f"upload bundle exceeds {MAX_UPLOAD_AGENT_FILES} files for agentic upload review")
    files: list[dict[str, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if len(text) > MAX_UPLOAD_AGENT_FILE_CHARS:
            raise ValueError(f"{path.name} is too large for agentic upload review")
        files.append({"filename": path.name, "content": text})
    if not files:
        raise ValueError("upload bundle contains no files for agentic upload review")
    return files


def _read_agent_summary(summary_path: Path, changed_files: list[str]) -> dict[str, Any]:
    fallback = {
        "content_summary": "Curator normalized an uploaded document for corpus review.",
        "draft_paths": _markdown_changed_files(changed_files),
    }
    if not summary_path.exists():
        return fallback
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    summary = payload.get("content_summary")
    draft_paths = payload.get("draft_paths")
    return {
        "content_summary": summary if isinstance(summary, str) and summary.strip() else fallback["content_summary"],
        "draft_paths": draft_paths if _valid_path_list(draft_paths) else fallback["draft_paths"],
    }


def _final_draft_paths(summary: dict[str, Any], changed_files: list[str]) -> list[str]:
    paths = summary.get("draft_paths")
    if _valid_path_list(paths):
        return list(paths)
    return _markdown_changed_files(changed_files)


def _markdown_changed_files(changed_files: list[str]) -> list[str]:
    return sorted(
        path
        for path in changed_files
        if path.endswith(".md") and not path.startswith(".") and _safe_repo_path(path)
    )


def _valid_path_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item and _safe_repo_path(item) for item in value
    )


def _forbidden_change_message(changed_files: list[str]) -> str | None:
    backup_or_temp_paths = forbidden_backup_or_temp_paths(changed_files)
    if backup_or_temp_paths:
        return "Codex upload review created backup or temporary files."
    for path in changed_files:
        if (
            path in FORBIDDEN_CHANGED_FILES
            or path.startswith(".env.")
            or path.startswith(".github/workflows/")
        ):
            return "Codex upload review changed forbidden files."
    return None


def _safe_repo_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value == path.as_posix()


def _changed_files(checkout: Path, env: dict[str, str]) -> list[str]:
    output = _git_output(["status", "--porcelain", "-uall"], cwd=checkout, env=env)
    files: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[-1]
        files.append(path)
    return sorted(set(files))


def _branch_changed_files(checkout: Path, env: dict[str, str], base_ref: str) -> list[str]:
    committed = _git_output(["diff", "--name-only", f"{base_ref}..HEAD"], cwd=checkout, env=env)
    return sorted(set(_changed_files(checkout, env)) | set(committed.splitlines()))


def _has_branch_commits(checkout: Path, env: dict[str, str], base_ref: str) -> bool:
    count = _git_output(["rev-list", "--count", f"{base_ref}..HEAD"], cwd=checkout, env=env).strip()
    return count not in {"", "0"}


def _diff_stat(
    checkout: Path,
    env: dict[str, str],
    changed_files: list[str],
    *,
    base_ref: str | None = None,
) -> str:
    diff_stat = ""
    if base_ref:
        diff_stat = _git_output(["diff", "--stat", f"{base_ref}..HEAD"], cwd=checkout, env=env)
    working_stat = _git_output(["diff", "--stat"], cwd=checkout, env=env)
    if working_stat:
        diff_stat = (diff_stat.rstrip() + "\n" + working_stat).lstrip()
    untracked = _git_output(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=checkout,
        env=env,
    )
    untracked_files = [path for path in untracked.splitlines() if path in changed_files]
    if not untracked_files:
        return diff_stat
    untracked_lines = "\n".join(f" {path} | untracked" for path in untracked_files)
    return (diff_stat.rstrip() + "\n" + untracked_lines + "\n").lstrip()


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


def _agent_observation(
    preview: UploadReviewPreview,
    *,
    status: str,
    message: str,
    model: str,
    attempts: int = 0,
    draft_paths: list[str] | None = None,
    changed_files: list[str] | None = None,
    diff_stat: str | None = None,
    validation_command: list[str],
    validation: subprocess.CompletedProcess[str] | None = None,
    transcript_path: Path | None = None,
    elapsed_seconds: float | None = None,
) -> UploadReviewObservation:
    return UploadReviewObservation(
        upload_id=preview.upload_id,
        action_id=preview.action_id,
        status=status,  # type: ignore[arg-type]
        decision="integrated" if status == "pass" else None,
        message=message,
        draft_paths=draft_paths or [],
        command=validation_command,
        returncode=validation.returncode if validation is not None else None,
        stdout_tail=_tail(validation.stdout) if validation is not None else "",
        stderr_tail=_tail(validation.stderr) if validation is not None else "",
        elapsed_seconds=elapsed_seconds,
        executor="codex_proxy",
        model=model,
        attempts=attempts,
        changed_files=changed_files or [],
        diff_stat=diff_stat,
        transcript_path=str(transcript_path) if transcript_path is not None else None,
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


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)[:120]
