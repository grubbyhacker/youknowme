from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import fcntl

from ykm.build import detect_secret, parse_frontmatter
from ykm.contracts import (
    CorpusChangeLogRecord,
    CorpusChangeRequest,
    CorpusChangeResponse,
    StagedUploadFile,
    UploadFileInput,
    UploadRequest,
    UploadResponse,
)
from ykm.logging import now_utc


INTAKE_SCHEMA_VERSION = "1"
IDEMPOTENCY_SCHEMA_VERSION = "1"
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


class IntakeIdempotencyConflict(IntakeError):
    pass


@dataclass(frozen=True)
class _PreparedFile:
    staged: StagedUploadFile
    content: str


@dataclass(frozen=True)
class _PreparedUpload:
    files: list[_PreparedFile]
    warnings: list[str]
    total_bytes: int
    suggested_tags: list[str]
    suggested_related: list[str]
    request_fingerprint: str


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
        prepared = prepare_upload(request)
        key_digest = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
        with self._idempotency_lock():
            record_path = self._idempotency_dir / f"{key_digest}.json"
            if record_path.exists():
                record = self._read_idempotency_record(record_path, key_digest)
                if record["request_fingerprint"] != prepared.request_fingerprint:
                    raise IntakeIdempotencyConflict(
                        "idempotency key was already used for a different upload request"
                    )
                response = UploadResponse.model_validate(record["response"])
                if record["state"] != "complete":
                    self._recover_incomplete_upload(
                        record_path,
                        record,
                        request,
                        prepared,
                        build_id=build_id,
                        auth_path=auth_path,
                        key_digest=key_digest,
                    )
                return response.model_copy(update={"replayed": True})

            upload_id = new_upload_id()
            response = UploadResponse(
                accepted=True,
                upload_id=upload_id,
                status="pending",
                file_count=len(prepared.files),
                total_bytes=prepared.total_bytes,
                warnings=prepared.warnings,
                staged_path=f"uploads/pending/{upload_id}",
            )
            record = {
                "schema_version": IDEMPOTENCY_SCHEMA_VERSION,
                "state": "creating",
                "idempotency_key_sha256": key_digest,
                "request_fingerprint": prepared.request_fingerprint,
                "response": response.model_dump(mode="json"),
            }
            atomic_write_json(record_path, record)
            self._write_upload_bundle(
                request,
                prepared,
                response,
                build_id=build_id,
                auth_path=auth_path,
                key_digest=key_digest,
            )
            record["state"] = "complete"
            atomic_write_json(record_path, record)
            return response

    @property
    def _idempotency_dir(self) -> Path:
        return self.root / "uploads" / "idempotency"

    @contextmanager
    def _idempotency_lock(self) -> Iterator[None]:
        self._idempotency_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._idempotency_dir, 0o700)
        lock_path = self._idempotency_dir / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_idempotency_record(
        self, path: Path, expected_key_digest: str
    ) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != IDEMPOTENCY_SCHEMA_VERSION
                or payload.get("state") not in {"creating", "complete"}
                or payload.get("idempotency_key_sha256") != expected_key_digest
                or not isinstance(payload.get("request_fingerprint"), str)
                or not isinstance(payload.get("response"), dict)
            ):
                raise ValueError("invalid idempotency record shape")
            response = UploadResponse.model_validate(payload["response"])
            if (
                re.fullmatch(r"upl_[A-Za-z0-9_]+", response.upload_id) is None
                or response.staged_path != f"uploads/pending/{response.upload_id}"
            ):
                raise ValueError("invalid idempotency response path")
        except (OSError, ValueError) as exc:
            raise IntakeError("upload idempotency record is invalid") from exc
        return payload

    def _recover_incomplete_upload(
        self,
        record_path: Path,
        record: dict[str, object],
        request: UploadRequest,
        prepared: _PreparedUpload,
        *,
        build_id: str | None,
        auth_path: str,
        key_digest: str,
    ) -> None:
        response = UploadResponse.model_validate(record["response"])
        existing = self._find_upload_bundle(
            response.upload_id, prepared.request_fingerprint
        )
        if existing is None:
            self._write_upload_bundle(
                request,
                prepared,
                response,
                build_id=build_id,
                auth_path=auth_path,
                key_digest=key_digest,
            )
        record["state"] = "complete"
        atomic_write_json(record_path, record)

    def _find_upload_bundle(
        self, upload_id: str, expected_fingerprint: str
    ) -> Path | None:
        for queue in ("pending", "claimed", "processed", "rejected", "archive", "deferred"):
            candidate = self.root / "uploads" / queue / upload_id
            if candidate.is_dir() and (candidate / "manifest.json").is_file():
                try:
                    manifest = json.loads(
                        (candidate / "manifest.json").read_text(encoding="utf-8")
                    )
                except (OSError, ValueError) as exc:
                    raise IntakeError("existing upload bundle manifest is invalid") from exc
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("upload_id") != upload_id
                    or manifest.get("request_fingerprint") != expected_fingerprint
                ):
                    raise IntakeError("existing upload bundle conflicts with idempotency record")
                return candidate
        return None

    def _write_upload_bundle(
        self,
        request: UploadRequest,
        prepared: _PreparedUpload,
        response: UploadResponse,
        *,
        build_id: str | None,
        auth_path: str,
        key_digest: str,
    ) -> None:
        pending_root = self.root / "uploads" / "pending"
        pending_root.mkdir(parents=True, exist_ok=True)
        pending_dir = pending_root / response.upload_id
        temporary_dir = pending_root / f".{response.upload_id}.{uuid4().hex}.tmp"
        if pending_dir.exists():
            raise IntakeError("upload bundle already exists without a valid idempotency record")
        for stale in pending_root.glob(f".{response.upload_id}.*.tmp"):
            if stale.is_dir():
                shutil.rmtree(stale)

        files_dir = temporary_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=False)
        try:
            for file in prepared.files:
                (files_dir / file.staged.stored_filename).write_text(
                    file.content, encoding="utf-8"
                )

            manifest = {
                "schema_version": INTAKE_SCHEMA_VERSION,
                "upload_id": response.upload_id,
                "timestamp": now_utc().isoformat(),
                "auth_path": auth_path,
                "build_id": build_id,
                "purpose": request.purpose,
                "suggested_type": request.suggested_type,
                "suggested_tags": prepared.suggested_tags,
                "suggested_related": prepared.suggested_related,
                "status": "pending",
                "file_count": len(prepared.files),
                "total_bytes": prepared.total_bytes,
                "files": [file.staged.model_dump(mode="json") for file in prepared.files],
                "warnings": prepared.warnings,
                "idempotency_key_sha256": key_digest,
                "request_fingerprint": prepared.request_fingerprint,
            }
            write_json(temporary_dir / "manifest.json", manifest)
            os.replace(temporary_dir, pending_dir)
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)

    def record_corpus_change(
        self,
        request: CorpusChangeRequest,
        *,
        build_id: str | None,
        auth_path: str = "mcp",
    ) -> CorpusChangeResponse:
        feedback_id = new_feedback_id()
        path = self.root / "feedback" / "feedback.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = CorpusChangeLogRecord(
            timestamp=now_utc(),
            feedback_id=feedback_id,
            auth_path=auth_path,
            build_id=build_id,
            intent=request.intent,
            instruction=request.instruction.strip(),
            source_id=request.source_id,
            section_id=request.section_id,
            result_ids=request.result_ids,
            upload_id=request.upload_id,
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")
        return CorpusChangeResponse(
            accepted=True,
            corpus_change_id=feedback_id,
            path="feedback/feedback.jsonl",
        )


def prepare_upload(request: UploadRequest) -> _PreparedUpload:
    if len(request.files) > MAX_UPLOAD_FILES:
        raise IntakeError(f"upload may include at most {MAX_UPLOAD_FILES} markdown files")

    prepared_files: list[_PreparedFile] = []
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
        prepared_files.append(
            _PreparedFile(
                staged=StagedUploadFile(
                    original_filename=file.filename,
                    stored_filename=stored_filename,
                    byte_count=len(encoded),
                    sha256=hashlib.sha256(encoded).hexdigest(),
                ),
                content=content,
            )
        )

    suggested_tags = sorted({tag.strip().lower() for tag in request.suggested_tags if tag})
    suggested_related = sorted(
        {related.strip() for related in request.suggested_related if related}
    )
    canonical_request = {
        "files": [
            {
                "original_filename": file.staged.original_filename,
                "stored_filename": file.staged.stored_filename,
                "content": file.content,
            }
            for file in prepared_files
        ],
        "purpose": request.purpose,
        "suggested_type": request.suggested_type,
        "suggested_tags": suggested_tags,
        "suggested_related": suggested_related,
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical_request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return _PreparedUpload(
        files=prepared_files,
        warnings=warnings,
        total_bytes=total_bytes,
        suggested_tags=suggested_tags,
        suggested_related=suggested_related,
        request_fingerprint=fingerprint,
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


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
