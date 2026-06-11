from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from curator.model_tasks import UploadReviewModelOutput
from curator.upload_draft import ID_RE


MAX_VALIDATE_OUTPUT_CHARS = 4000
MAX_DRAFT_FILES = 10
VALIDATE_TIMEOUT_SECONDS = 120
POLICY_PATH = Path(".ykm/corpus-policy.yaml")
IGNORED_COPY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}
FORBIDDEN_VALIDATE_ENV = {
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "YKM_GITHUB_PRIVATE_KEY_PATH",
    "YKM_CF_ACCESS_CLIENT_SECRET",
}


class UploadReviewObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    action_id: str | None = None
    status: Literal["pass", "fail", "skip"]
    decision: str | None = None
    message: str
    draft_paths: list[str] = Field(default_factory=list)
    policy_roots_add: list[str] = Field(default_factory=list)
    policy_types_add: list[str] = Field(default_factory=list)
    policy_tags_add: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    returncode: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    elapsed_seconds: float | None = None
    executor: str | None = None
    model: str | None = None
    attempts: int = 0
    changed_files: list[str] = Field(default_factory=list)
    diff_stat: str | None = None
    transcript_path: str | None = None


def observe_upload_review_draft(
    *,
    corpus_checkout: Path,
    output: UploadReviewModelOutput,
    action_id: str | None = None,
) -> UploadReviewObservation:
    if output.decision != "integrated":
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="skip",
            decision=output.decision,
            message="upload review model did not produce an integrated corpus draft",
            draft_paths=[file.path for file in output.files],
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
        )
    if not output.files:
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="fail",
            decision=output.decision,
            message="integrated upload review output contains no draft files",
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
        )
    if len(output.files) > MAX_DRAFT_FILES:
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="fail",
            decision=output.decision,
            message=f"integrated upload review output exceeds {MAX_DRAFT_FILES} draft files",
            draft_paths=[file.path for file in output.files[:MAX_DRAFT_FILES]],
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
        )

    try:
        safe_paths = [_safe_draft_path(file.path) for file in output.files]
        _validate_policy_values(output.policy_patch.corpus_roots_add, name="corpus_roots_add")
        _validate_policy_values(output.policy_patch.allowed_types_add, name="allowed_types_add")
        _validate_policy_values(output.policy_patch.allowed_tags_add, name="allowed_tags_add")
    except ValueError as exc:
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="fail",
            decision=output.decision,
            message=str(exc),
            draft_paths=[file.path for file in output.files],
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
        )

    if not corpus_checkout.exists() or not corpus_checkout.is_dir():
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="fail",
            decision=output.decision,
            message=f"corpus checkout is not a directory: {corpus_checkout}",
            draft_paths=[str(path) for path in safe_paths],
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
        )

    command = ["mise", "run", "validate"]
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="ykm-upload-observe-") as temp_root:
            temp_checkout = Path(temp_root) / "ykmcorpus"
            copy_corpus_checkout(corpus_checkout, temp_checkout)
            apply_upload_review_draft_to_checkout(temp_checkout, output)
            trust = _trust_mise_config(temp_checkout)
            if trust.returncode != 0:
                return UploadReviewObservation(
                    upload_id=output.upload_id,
                    action_id=action_id,
                    status="fail",
                    decision=output.decision,
                    message="corpus validation trust step failed",
                    draft_paths=[str(path) for path in safe_paths],
                    policy_roots_add=output.policy_patch.corpus_roots_add,
                    policy_types_add=output.policy_patch.allowed_types_add,
                    policy_tags_add=output.policy_patch.allowed_tags_add,
                    command=["mise", "trust", "--yes", str(temp_checkout / "mise.toml")],
                    returncode=trust.returncode,
                    stdout_tail=_tail(trust.stdout),
                    stderr_tail=_tail(trust.stderr),
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )
            completed = subprocess.run(
                command,
                cwd=temp_checkout,
                capture_output=True,
                text=True,
                timeout=VALIDATE_TIMEOUT_SECONDS,
                env=_validation_env(),
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="fail",
            decision=output.decision,
            message=f"corpus validation timed out after {VALIDATE_TIMEOUT_SECONDS}s",
            draft_paths=[str(path) for path in safe_paths],
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
            command=command,
            stdout_tail=_tail(exc.stdout or ""),
            stderr_tail=_tail(exc.stderr or ""),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    except Exception as exc:  # noqa: BLE001 - observe failures must be structured in the report.
        return UploadReviewObservation(
            upload_id=output.upload_id,
            action_id=action_id,
            status="fail",
            decision=output.decision,
            message=f"corpus validation observe failed before command execution: {exc}",
            draft_paths=[str(path) for path in safe_paths],
            policy_roots_add=output.policy_patch.corpus_roots_add,
            policy_types_add=output.policy_patch.allowed_types_add,
            policy_tags_add=output.policy_patch.allowed_tags_add,
            command=command,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    passed = completed.returncode == 0
    return UploadReviewObservation(
        upload_id=output.upload_id,
        action_id=action_id,
        status="pass" if passed else "fail",
        decision=output.decision,
        message="corpus validation passed" if passed else "corpus validation failed",
        draft_paths=[str(path) for path in safe_paths],
        policy_roots_add=output.policy_patch.corpus_roots_add,
        policy_types_add=output.policy_patch.allowed_types_add,
        policy_tags_add=output.policy_patch.allowed_tags_add,
        command=command,
        returncode=completed.returncode,
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def copy_corpus_checkout(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*IGNORED_COPY_NAMES),
        symlinks=False,
    )


def apply_upload_review_draft_to_checkout(
    checkout: Path,
    output: UploadReviewModelOutput,
) -> list[str]:
    if output.decision != "integrated":
        raise ValueError("only integrated upload review drafts can be applied")
    if not output.files:
        raise ValueError("integrated upload review output contains no draft files")
    if len(output.files) > MAX_DRAFT_FILES:
        raise ValueError(f"integrated upload review output exceeds {MAX_DRAFT_FILES} draft files")
    safe_paths = [_safe_draft_path(file.path) for file in output.files]
    _validate_policy_values(output.policy_patch.corpus_roots_add, name="corpus_roots_add")
    _validate_policy_values(output.policy_patch.allowed_types_add, name="allowed_types_add")
    _validate_policy_values(output.policy_patch.allowed_tags_add, name="allowed_tags_add")
    _apply_policy_patch(checkout / POLICY_PATH, output)
    for draft_file, relative_path in zip(output.files, safe_paths, strict=True):
        destination = checkout / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_normalized_markdown(draft_file.content), encoding="utf-8")
    return [str(path) for path in safe_paths]


def _safe_draft_path(path: str) -> PurePosixPath:
    relative = PurePosixPath(path)
    if relative.is_absolute():
        raise ValueError(f"draft path must be relative: {path}")
    if relative.suffix != ".md":
        raise ValueError(f"draft path must be a markdown file: {path}")
    if len(relative.parts) < 2:
        raise ValueError(f"draft path must include a corpus root and filename: {path}")
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise ValueError(f"draft path contains an unsafe segment: {path}")
        stem = part.removesuffix(".md")
        if not ID_RE.match(stem):
            raise ValueError(f"draft path segment must be kebab-case: {path}")
    return relative


def _validate_policy_values(values: list[str], *, name: str) -> None:
    invalid = [value for value in values if not ID_RE.match(value)]
    if invalid:
        raise ValueError(f"policy patch {name} contains invalid values: {invalid}")


def _apply_policy_patch(policy_path: Path, output: UploadReviewModelOutput) -> None:
    if not policy_path.exists():
        raise FileNotFoundError(f"corpus policy file missing: {policy_path}")
    text = policy_path.read_text(encoding="utf-8")
    text = _append_yaml_list_values(text, "corpus_roots", output.policy_patch.corpus_roots_add)
    text = _append_yaml_list_values(text, "allowed_types", output.policy_patch.allowed_types_add)
    text = _append_yaml_list_values(text, "allowed_tags", output.policy_patch.allowed_tags_add)
    policy_path.write_text(text, encoding="utf-8")


def _trust_mise_config(checkout: Path) -> subprocess.CompletedProcess[str]:
    mise_toml = checkout / "mise.toml"
    if not mise_toml.exists():
        return subprocess.CompletedProcess(
            args=["mise", "trust", "--yes", str(mise_toml)],
            returncode=0,
            stdout="",
            stderr="",
        )
    return subprocess.run(
        ["mise", "trust", "--yes", str(mise_toml)],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=VALIDATE_TIMEOUT_SECONDS,
        env=_validation_env(),
        check=False,
    )


def _append_yaml_list_values(text: str, key: str, values: list[str]) -> str:
    additions = sorted(set(values))
    if not additions:
        return text

    lines = text.splitlines()
    key_index = next((index for index, line in enumerate(lines) if line.strip() == f"{key}:"), None)
    if key_index is None:
        raise ValueError(f"corpus policy missing {key} list")

    insert_index = key_index + 1
    existing: set[str] = set()
    while insert_index < len(lines):
        line = lines[insert_index]
        if line and not line.startswith(" "):
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            existing.add(stripped[2:].strip())
        insert_index += 1

    missing = [value for value in additions if value not in existing]
    if not missing:
        return text if text.endswith("\n") else text + "\n"
    rendered = [f"  - {value}" for value in missing]
    updated = lines[:insert_index] + rendered + lines[insert_index:]
    return "\n".join(updated) + "\n"


def _normalized_markdown(content: str) -> str:
    return content if content.endswith("\n") else content + "\n"


def _tail(value: str) -> str:
    if len(value) <= MAX_VALIDATE_OUTPUT_CHARS:
        return value
    return value[-MAX_VALIDATE_OUTPUT_CHARS:]


def _validation_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in FORBIDDEN_VALIDATE_ENV:
        env.pop(name, None)
    return env
