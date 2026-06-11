from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from curator.markers import render_action_markers
from curator.model_tasks import UploadReviewModelOutput
from curator.models import (
    ActionEvidence,
    ExecutionIntent,
    ExecutionResult,
    ProposedAction,
    UploadReviewPreview,
)
from curator.upload_observe import apply_upload_review_draft_to_checkout


GIT_TIMEOUT_SECONDS = 120
GIT_AUTHOR_NAME = "YouKnowMe Curator"
GIT_AUTHOR_EMAIL = "youknowme-curator@users.noreply.github.com"


def execute_upload_review_pr(
    *,
    run_id: str,
    broker_remote_url: str,
    broker_adapter,
    preview: UploadReviewPreview,
    output: UploadReviewModelOutput,
    on_branch_pushed: Callable[[ExecutionIntent], None] | None = None,
) -> ExecutionResult:
    intent = upload_review_pull_intent(
        run_id=run_id,
        preview=preview,
        content_summary=output.content_summary,
    )
    if output.upload_id != preview.upload_id:
        return _failed_result(intent, f"upload review output mismatch for {preview.upload_id}")
    if output.decision != "integrated":
        return _failed_result(intent, "only integrated upload review outputs can create PRs")
    if not broker_remote_url:
        return _failed_result(intent, "broker git remote URL is required for upload PR creation")
    try:
        with tempfile.TemporaryDirectory(prefix="ykm-upload-pr-") as temp_root:
            checkout = Path(temp_root) / "ykmcorpus"
            askpass = _write_askpass(Path(temp_root))
            env = _git_env(askpass)
            _run_git(["clone", "--depth=1", broker_remote_url, str(checkout)], cwd=None, env=env)
            _run_git(["checkout", "-B", preview.branch], cwd=checkout, env=env)
            draft_paths = apply_upload_review_draft_to_checkout(checkout, output)
            _run_validate(checkout)
            _run_git(["add", ".ykm/corpus-policy.yaml", *draft_paths], cwd=checkout, env=env)
            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=checkout,
                env=env,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
            if diff.returncode == 0:
                return _failed_result(intent, "upload review draft produced no commit changes")
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
                env=env,
            )
            _run_git(["push", "origin", f"HEAD:refs/heads/{preview.branch}"], cwd=checkout, env=env)
            if on_branch_pushed is not None:
                on_branch_pushed(intent)
    except Exception as exc:  # noqa: BLE001 - live execution failures must be reportable.
        return _failed_result(intent, f"upload review PR branch push failed: {exc}")
    return broker_adapter.create_pull(intent)


def upload_review_pull_intent(
    *,
    run_id: str,
    preview: UploadReviewPreview,
    content_summary: str | None = None,
) -> ExecutionIntent:
    action = ProposedAction(
        action_id=preview.action_id,
        action_type="corpus_pr",
        classification="upload_review",
        idempotency_key="corpus_pr:" + preview.idempotency_key.split(":", maxsplit=1)[-1],
        evidence=ActionEvidence(upload_ids=[preview.upload_id]),
        target_repo="grubbyhacker/ykmcorpus",
    )
    return ExecutionIntent(
        action_id=preview.action_id,
        operation="pull.create",
        idempotency_key=preview.idempotency_key,
        target_repo="grubbyhacker/ykmcorpus",
        branch=preview.branch,
        evidence=ActionEvidence(upload_ids=[preview.upload_id]),
        title=_upload_review_pr_title(preview),
        body=_upload_review_pr_body(run_id, preview, action, content_summary=content_summary),
        labels=["ykm-curator", "upload"],
    )


def _upload_review_pr_title(preview: UploadReviewPreview) -> str:
    title = f"YouKnowMe Curator upload review: {preview.upload_id}"
    if not preview.draft_paths:
        return title
    primary_path = preview.draft_paths[0]
    if len(preview.draft_paths) == 1:
        return f"YouKnowMe Curator upload review: {primary_path}"
    return f"YouKnowMe Curator upload review: {primary_path} (+{len(preview.draft_paths) - 1})"


def _upload_review_pr_body(
    run_id: str,
    preview: UploadReviewPreview,
    marker_action: ProposedAction,
    *,
    content_summary: str | None = None,
) -> str:
    marker_block = render_action_markers(run_id, marker_action)
    marker_block = marker_block.replace(
        f"YKM-Curator-Idempotency-Key: {marker_action.idempotency_key}",
        f"YKM-Curator-Idempotency-Key: {preview.idempotency_key}",
    )
    summary = _single_line_summary(content_summary)
    return (
        "# YouKnowMe Curator upload review\n\n"
        f"- Upload: `{preview.upload_id}`\n"
        f"- Content: {summary}\n"
        f"{_upload_review_pr_page_lines(preview)}"
        f"- Branch: `{preview.branch}`\n"
        "- Corpus validation: passed before PR creation.\n\n"
        "This PR contains normalized corpus markdown and additive policy changes proposed from "
        "the upload-review model. It does not include intake excerpts in the PR body.\n\n"
        "## Curator Markers\n\n"
        f"{marker_block}"
    )


def _upload_review_pr_page_lines(preview: UploadReviewPreview) -> str:
    if not preview.draft_paths:
        return ""
    if len(preview.draft_paths) == 1:
        return f"- Page: `{preview.draft_paths[0]}`\n"
    pages = ", ".join(f"`{path}`" for path in preview.draft_paths)
    return f"- Pages: {pages}\n"


def _single_line_summary(content_summary: str | None) -> str:
    summary = " ".join((content_summary or "No upload summary supplied.").split())
    return summary[:300]


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


def _run_validate(checkout: Path) -> None:
    mise_toml = checkout / "mise.toml"
    if mise_toml.exists():
        trust = subprocess.run(
            ["mise", "trust", "--yes", str(mise_toml)],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if trust.returncode != 0:
            detail = (trust.stderr or trust.stdout).strip()[:500]
            raise RuntimeError(f"mise trust failed with exit {trust.returncode}: {detail}")
    result = subprocess.run(
        ["mise", "run", "validate"],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise RuntimeError(f"mise run validate failed with exit {result.returncode}: {detail}")


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
