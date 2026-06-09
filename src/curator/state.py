from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from curator.models import (
    CURATOR_SCHEMA_VERSION,
    DEFAULT_STALE_LOCK_TIMEOUT_SECONDS,
    UPLOAD_QUEUE_DIRS,
    ActionEvidence,
    CuratorState,
    FeedbackCheckpoint,
    FeedbackDecision,
    FeedbackInputRecord,
    FeedbackWindow,
    FeedbackWindowReadResult,
    InputRecordError,
    UploadBundleSnapshot,
    UploadCuratorMetadata,
    UploadQueueSnapshot,
)


class CuratorLockError(RuntimeError):
    pass


class CuratorLiveLockError(CuratorLockError):
    pass


class CuratorStaleLockError(CuratorLockError):
    pass


class CuratorRunLock:
    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        stale_timeout_seconds: int = DEFAULT_STALE_LOCK_TIMEOUT_SECONDS,
        recover_stale: bool = False,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.stale_timeout_seconds = stale_timeout_seconds
        self.recover_stale = recover_stale
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        payload = {
            "schema_version": CURATOR_SCHEMA_VERSION,
            "run_id": self.run_id,
            "pid": os.getpid(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileExistsError:
            self._handle_existing_lock()
            try:
                fd = os.open(self.path, flags, 0o644)
            except FileExistsError as retry_exc:
                raise CuratorLiveLockError(f"curator lock is already held: {self.path}") from retry_exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self.acquired = False

    def __enter__(self) -> CuratorRunLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def _handle_existing_lock(self) -> None:
        try:
            age_seconds = datetime.now(UTC).timestamp() - self.path.stat().st_mtime
        except OSError as exc:
            raise CuratorLiveLockError(f"curator lock cannot be inspected: {self.path}") from exc
        if age_seconds < self.stale_timeout_seconds:
            raise CuratorLiveLockError(f"curator lock is already held: {self.path}")
        if not self.recover_stale:
            raise CuratorStaleLockError(
                f"stale curator lock requires explicit recovery mode: {self.path}"
            )
        self.path.unlink()


def read_curator_state(path: Path) -> CuratorState:
    if not path.exists():
        return CuratorState()
    return CuratorState.model_validate_json(path.read_text(encoding="utf-8"))


def write_curator_state(path: Path, state: CuratorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def freeze_feedback_window(feedback_path: Path, state: CuratorState) -> FeedbackWindow:
    start_offset = state.feedback_checkpoint.byte_offset
    end_offset = feedback_path.stat().st_size if feedback_path.exists() else 0
    if start_offset > end_offset:
        start_offset = 0
    return FeedbackWindow(start_offset=start_offset, end_offset=end_offset)


def read_feedback_window(feedback_path: Path, window: FeedbackWindow) -> list[dict[str, Any]]:
    result = read_feedback_window_result(feedback_path, window)
    if result.errors:
        first = result.errors[0]
        raise ValueError(f"invalid feedback record on line {first.line_number}: {first.message}")
    return result.records


def read_feedback_window_result(
    feedback_path: Path, window: FeedbackWindow, *, max_errors: int = 20
) -> FeedbackWindowReadResult:
    if not feedback_path.exists() or window.end_offset <= window.start_offset:
        return FeedbackWindowReadResult()
    records: list[dict[str, Any]] = []
    errors: list[InputRecordError] = []
    try:
        with feedback_path.open("rb") as handle:
            handle.seek(window.start_offset)
            remaining = window.end_offset - window.start_offset
            data = handle.read(remaining)
    except OSError as exc:
        return FeedbackWindowReadResult(
            errors=[
                InputRecordError(
                    path=str(feedback_path),
                    category="read_error",
                    message=str(exc),
                )
            ]
        )
    line_number = 0
    byte_offset = window.start_offset
    for line in data.splitlines(keepends=True):
        line_number += 1
        current_offset = byte_offset
        byte_offset += len(line)
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except JSONDecodeError as exc:
            _append_input_error(
                errors,
                InputRecordError(
                    path=str(feedback_path),
                    line_number=line_number,
                    byte_offset=current_offset,
                    category="invalid_json",
                    message=str(exc),
                ),
                max_errors=max_errors,
            )
            continue
        if not isinstance(payload, dict):
            _append_input_error(
                errors,
                InputRecordError(
                    path=str(feedback_path),
                    line_number=line_number,
                    byte_offset=current_offset,
                    category="invalid_type",
                    message="feedback JSONL record must be an object",
                ),
                max_errors=max_errors,
            )
            continue
        try:
            FeedbackInputRecord.model_validate(payload)
        except ValidationError as exc:
            _append_input_error(
                errors,
                InputRecordError(
                    path=str(feedback_path),
                    line_number=line_number,
                    byte_offset=current_offset,
                    category="invalid_schema",
                    message=str(exc),
                ),
                max_errors=max_errors,
            )
            continue
        records.append(payload)
    return FeedbackWindowReadResult(records=records, errors=errors)


def read_feedback_records_by_id(
    feedback_path: Path,
    feedback_ids: set[str],
    *,
    max_errors: int = 20,
) -> FeedbackWindowReadResult:
    if not feedback_ids or not feedback_path.exists():
        return FeedbackWindowReadResult()
    records_by_id: dict[str, dict[str, Any]] = {}
    errors: list[InputRecordError] = []
    try:
        with feedback_path.open("rb") as handle:
            byte_offset = 0
            for line_number, line in enumerate(handle, start=1):
                current_offset = byte_offset
                byte_offset += len(line)
                if not line.strip():
                    continue
                payload = _decode_feedback_record(
                    line=line,
                    feedback_path=feedback_path,
                    line_number=line_number,
                    byte_offset=current_offset,
                    errors=errors,
                    max_errors=max_errors,
                )
                if payload is None:
                    continue
                feedback_id = payload.get("feedback_id")
                if feedback_id in feedback_ids:
                    records_by_id[feedback_id] = payload
    except OSError as exc:
        return FeedbackWindowReadResult(
            errors=[
                InputRecordError(
                    path=str(feedback_path),
                    category="read_error",
                    message=str(exc),
                )
            ]
        )
    return FeedbackWindowReadResult(
        records=[records_by_id[feedback_id] for feedback_id in sorted(records_by_id)],
        errors=errors,
    )


def _decode_feedback_record(
    *,
    line: bytes,
    feedback_path: Path,
    line_number: int,
    byte_offset: int,
    errors: list[InputRecordError],
    max_errors: int,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except JSONDecodeError as exc:
        _append_input_error(
            errors,
            InputRecordError(
                path=str(feedback_path),
                line_number=line_number,
                byte_offset=byte_offset,
                category="invalid_json",
                message=str(exc),
            ),
            max_errors=max_errors,
        )
        return None
    if not isinstance(payload, dict):
        _append_input_error(
            errors,
            InputRecordError(
                path=str(feedback_path),
                line_number=line_number,
                byte_offset=byte_offset,
                category="invalid_type",
                message="feedback JSONL record must be an object",
            ),
            max_errors=max_errors,
        )
        return None
    try:
        FeedbackInputRecord.model_validate(payload)
    except ValidationError as exc:
        _append_input_error(
            errors,
            InputRecordError(
                path=str(feedback_path),
                line_number=line_number,
                byte_offset=byte_offset,
                category="invalid_schema",
                message=str(exc),
            ),
            max_errors=max_errors,
        )
        return None
    return payload


def _append_input_error(
    errors: list[InputRecordError], error: InputRecordError, *, max_errors: int
) -> None:
    if len(errors) < max_errors:
        errors.append(error)


def advanced_state(run_id: str, state: CuratorState, window: FeedbackWindow) -> CuratorState:
    return CuratorState(
        last_completed_run_id=run_id,
        feedback_checkpoint=FeedbackCheckpoint(
            path=state.feedback_checkpoint.path,
            byte_offset=window.end_offset,
        ),
        updated_at=datetime.now(UTC),
    )


def load_latest_feedback_decisions(path: Path) -> dict[str, FeedbackDecision]:
    latest: dict[str, tuple[datetime, int, FeedbackDecision]] = {}
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                decision = FeedbackDecision.model_validate_json(line)
            except (ValidationError, ValueError) as exc:
                raise ValueError(f"invalid feedback decision on line {line_no}: {exc}") from exc
            current = latest.get(decision.feedback_id)
            if current is None or (decision.timestamp, line_no) >= (current[0], current[1]):
                latest[decision.feedback_id] = (decision.timestamp, line_no, decision)
    return {feedback_id: entry[2] for feedback_id, entry in latest.items()}


def snapshot_upload_queue(intake_path: Path) -> UploadQueueSnapshot:
    uploads = intake_path / "uploads"
    counts: dict[str, int] = {}
    pending_uploads: list[str] = []
    bundles: list[UploadBundleSnapshot] = []
    for state in UPLOAD_QUEUE_DIRS:
        state_dir = uploads / state
        if not state_dir.exists():
            counts[state] = 0
            continue
        state_bundles = sorted(path for path in state_dir.iterdir() if path.is_dir())
        counts[state] = len(state_bundles)
        if state == "pending":
            pending_uploads = [path.name for path in state_bundles[:50]]
        for bundle_path in state_bundles:
            bundles.append(_snapshot_upload_bundle(bundle_path, state))
    return UploadQueueSnapshot(counts=counts, pending_uploads=pending_uploads, bundles=bundles)


def _snapshot_upload_bundle(bundle_path: Path, queue: str) -> UploadBundleSnapshot:
    manifest_path = bundle_path / "manifest.json"
    manifest_upload_id, manifest_error = _read_upload_manifest(manifest_path)
    metadata_path = bundle_path / "curator.json"
    metadata: UploadCuratorMetadata | None = None
    metadata_error: str | None = None
    if metadata_path.exists():
        try:
            metadata = UploadCuratorMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
            if manifest_upload_id is not None and metadata.upload_id != manifest_upload_id:
                metadata_error = (
                    "upload curator metadata upload_id does not match manifest upload_id"
                )
        except (OSError, ValidationError, ValueError) as exc:
            metadata_error = str(exc)
    return UploadBundleSnapshot(
        upload_id=metadata.upload_id if metadata is not None else manifest_upload_id or bundle_path.name,
        queue=queue,  # type: ignore[arg-type]
        path=str(bundle_path),
        has_manifest=manifest_path.exists(),
        manifest_upload_id=manifest_upload_id,
        manifest_error=manifest_error,
        has_curator_metadata=metadata_path.exists(),
        curator_metadata=metadata,
        metadata_error=metadata_error,
    )


def _read_upload_manifest(path: Path) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "upload manifest must be a JSON object"
    upload_id = payload.get("upload_id")
    if not isinstance(upload_id, str) or not upload_id:
        return None, "upload manifest upload_id must be a non-empty string"
    return upload_id, None


def deterministic_idempotency_key(action_type: str, evidence: ActionEvidence) -> str:
    parts = [
        f"feedback={','.join(sorted(set(evidence.feedback_ids)))}",
        f"upload={','.join(sorted(set(evidence.upload_ids)))}",
        f"source={','.join(sorted(set(evidence.source_ids)))}",
        f"section={','.join(sorted(set(evidence.section_ids)))}",
        f"result={','.join(sorted(set(evidence.result_ids)))}",
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{action_type}:{digest}"
