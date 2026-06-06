from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ykm.build import detect_secret, parse_frontmatter
from ykm.contracts import (
    FeedbackLogRecord,
    FeedbackRequest,
    FeedbackResponse,
    StagedUploadFile,
    UploadFileInput,
    UploadRequest,
    UploadResponse,
)
from ykm.logging import now_utc


INTAKE_SCHEMA_VERSION = "1"
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_FILE_BYTES = 20 * 1024
MAX_UPLOAD_TOTAL_BYTES = 80 * 1024
ALLOWED_FRONTMATTER_FIELDS = {
    "id",
    "aliases",
    "type",
    "tags",
    "related",
    "delivery_mode",
}
UNSAFE_MARKDOWN_PATTERNS = [
    re.compile(r"(?is)<\s*script\b"),
    re.compile(r"(?is)<\s*!doctype\s+html\b"),
    re.compile(r"(?is)<\s*html\b"),
    re.compile(r"(?i)data\s*:[a-z0-9.+-]+/[a-z0-9.+-]+;base64,"),
]


class IntakeError(ValueError):
    pass


class IntakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def stage_upload(
        self,
        request: UploadRequest,
        *,
        build_id: str | None,
        auth_path: str = "mcp",
    ) -> UploadResponse:
        if len(request.files) > MAX_UPLOAD_FILES:
            raise IntakeError(f"upload may include at most {MAX_UPLOAD_FILES} markdown files")

        upload_id = new_upload_id()
        pending_dir = self.root / "uploads" / "pending" / upload_id
        files_dir = pending_dir / "files"
        staged_files: list[StagedUploadFile] = []
        warnings: list[str] = []
        total_bytes = 0
        used_names: set[str] = set()

        for file in request.files:
            stored_filename = normalized_markdown_filename(file.filename)
            if stored_filename in used_names:
                stem = stored_filename.removesuffix(".md")
                suffix = 2
                while f"{stem}-{suffix}.md" in used_names:
                    suffix += 1
                stored_filename = f"{stem}-{suffix}.md"
            used_names.add(stored_filename)

            content = normalize_markdown_content(file)
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_UPLOAD_FILE_BYTES:
                raise IntakeError(
                    f"{file.filename} exceeds {MAX_UPLOAD_FILE_BYTES} bytes after UTF-8 encoding"
                )
            total_bytes += len(encoded)
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise IntakeError(f"upload exceeds {MAX_UPLOAD_TOTAL_BYTES} total bytes")

            validate_markdown_content(content, file.filename)
            warnings.extend(frontmatter_warnings(content, stored_filename))
            staged_files.append(
                StagedUploadFile(
                    original_filename=file.filename,
                    stored_filename=stored_filename,
                    byte_count=len(encoded),
                    sha256=hashlib.sha256(encoded).hexdigest(),
                )
            )

        files_dir.mkdir(parents=True, exist_ok=False)
        for file, staged in zip(request.files, staged_files, strict=True):
            content = normalize_markdown_content(file)
            (files_dir / staged.stored_filename).write_text(content, encoding="utf-8")

        manifest = {
            "schema_version": INTAKE_SCHEMA_VERSION,
            "upload_id": upload_id,
            "timestamp": now_utc().isoformat(),
            "auth_path": auth_path,
            "build_id": build_id,
            "purpose": request.purpose,
            "suggested_type": request.suggested_type,
            "suggested_tags": sorted({tag.strip().lower() for tag in request.suggested_tags if tag}),
            "suggested_related": sorted(
                {related.strip() for related in request.suggested_related if related}
            ),
            "status": "pending",
            "file_count": len(staged_files),
            "total_bytes": total_bytes,
            "files": [file.model_dump(mode="json") for file in staged_files],
            "warnings": warnings,
        }
        write_json(pending_dir / "manifest.json", manifest)

        return UploadResponse(
            accepted=True,
            upload_id=upload_id,
            status="pending",
            file_count=len(staged_files),
            total_bytes=total_bytes,
            warnings=warnings,
            staged_path=f"uploads/pending/{upload_id}",
        )

    def record_feedback(
        self,
        request: FeedbackRequest,
        *,
        build_id: str | None,
        auth_path: str = "mcp",
    ) -> FeedbackResponse:
        feedback_id = new_feedback_id()
        path = self.root / "feedback" / "feedback.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = FeedbackLogRecord(
            timestamp=now_utc(),
            feedback_id=feedback_id,
            auth_path=auth_path,
            build_id=build_id,
            category=request.category,
            comment=request.comment.strip(),
            source_id=request.source_id,
            section_id=request.section_id,
            result_ids=request.result_ids,
            upload_id=request.upload_id,
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")
        return FeedbackResponse(
            accepted=True,
            feedback_id=feedback_id,
            path="feedback/feedback.jsonl",
        )


def normalized_markdown_filename(filename: str) -> str:
    raw = filename.strip().replace("\\", "/")
    name = Path(raw).name
    if not name or name in {".", ".."} or "/" in raw.removeprefix(name):
        raise IntakeError("filename must be a simple markdown filename")
    if Path(name).suffix.lower() != ".md":
        raise IntakeError(f"only .md files are accepted: {filename}")
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    if not stem:
        raise IntakeError(f"filename must contain at least one alphanumeric character: {filename}")
    return f"{stem[:80]}.md"


def normalize_markdown_content(file: UploadFileInput) -> str:
    content = file.content.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in content:
        raise IntakeError(f"{file.filename} contains NUL bytes")
    if has_unsafe_control_chars(content):
        raise IntakeError(f"{file.filename} contains unsupported control characters")
    return content


def validate_markdown_content(content: str, filename: str) -> None:
    secret_reason = detect_secret(content)
    if secret_reason:
        raise IntakeError(f"{filename} rejected: {secret_reason}")
    for pattern in UNSAFE_MARKDOWN_PATTERNS:
        if pattern.search(content):
            raise IntakeError(f"{filename} contains unsupported HTML/script or embedded data content")


def frontmatter_warnings(content: str, filename: str) -> list[str]:
    metadata, _body = parse_frontmatter(content)
    unsupported = sorted(set(metadata) - ALLOWED_FRONTMATTER_FIELDS)
    if not unsupported:
        return []
    return [
        f"{filename}: unsupported frontmatter fields preserved for review: "
        + ", ".join(unsupported)
    ]


def has_unsafe_control_chars(content: str) -> bool:
    return any((ord(char) < 32 and char not in "\n\t") for char in content)


def new_upload_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"upl_{timestamp}_{uuid4().hex[:8]}"


def new_feedback_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"fb_{timestamp}_{uuid4().hex[:8]}"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
