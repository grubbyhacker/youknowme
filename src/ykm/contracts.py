from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1"


class SourceDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    aliases: list[str] = Field(default_factory=list)
    source_path: str
    title: str
    type: str = "note"
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    body: str


class ChunkRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_id: str
    aliases: list[str] = Field(default_factory=list)
    source_path: str
    section_id: str
    section_heading: str
    heading_path: list[str] = Field(default_factory=list)
    type: str = "note"
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    text: str
    parent_text: str
    ordinal: int
    start_line: int | None = None
    end_line: int | None = None


class BuildWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    code: str
    message: str


class QuarantineRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    reason: str


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    build_id: str
    source_commit: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    created_at: datetime
    chunk_count: int
    quarantined_count: int = 0
    warning_count: int = 0


class BuildOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: ArtifactManifest
    warnings: list[BuildWarning] = Field(default_factory=list)
    quarantined: list[QuarantineRecord] = Field(default_factory=list)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    type: str | None = None
    tags: list[str] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    source: str | None = None
    limit: int = Field(default=5, ge=1, le=10)


class SourcePointer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_path: str
    section_id: str


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    source_id: str
    source_path: str
    section_id: str
    parent_section: str
    matched_chunk: str
    returned_content: str
    tags: list[str]
    type: str
    score: float
    disambiguation_hint: str
    related: list[str] = Field(default_factory=list)
    truncated: bool = False
    retrieve_pointer: SourcePointer


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[QueryResult]
    build_id: str
    source_commit: str
    warnings: list[str] = Field(default_factory=list)


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: str
    kind: Literal["source_id", "section_id", "path"] = "source_id"


class RetrieveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    locator: str
    kind: str
    source_id: str | None = None
    source_path: str | None = None
    section_id: str | None = None
    content: str | None = None
    build_id: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    service: str = "YouKnowMe"
    index_loaded: bool
    source_commit: str | None = None
    build_id: str | None = None
    embedding_model: str | None = None
    created_at: datetime | None = None


class UploadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[UploadFileInput] = Field(min_length=1, max_length=10)
    purpose: str | None = Field(default=None, max_length=500)
    suggested_type: str | None = Field(default=None, max_length=80)
    suggested_tags: list[str] = Field(default_factory=list, max_length=20)
    suggested_related: list[str] = Field(default_factory=list, max_length=20)


class StagedUploadFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_filename: str
    stored_filename: str
    byte_count: int
    sha256: str


class UploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    upload_id: str
    status: Literal["pending"]
    file_count: int
    total_bytes: int
    warnings: list[str] = Field(default_factory=list)
    staged_path: str


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "missing_content",
        "wrong_content",
        "stale_content",
        "unclear_content",
        "agent_note",
    ]
    comment: str = Field(min_length=1, max_length=2000)
    source_id: str | None = Field(default=None, max_length=200)
    section_id: str | None = Field(default=None, max_length=200)
    result_ids: list[str] = Field(default_factory=list, max_length=10)
    upload_id: str | None = Field(default=None, max_length=80)


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    feedback_id: str
    path: str


class QueryLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    event: str
    latency_ms: float
    auth_path: str
    build_id: str | None
    result_source_ids: list[str] = Field(default_factory=list)
    result_count: int = 0
    error: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class FeedbackLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    event: Literal["feedback"] = "feedback"
    feedback_id: str
    auth_path: str
    build_id: str | None
    category: str
    comment: str
    source_id: str | None = None
    section_id: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    upload_id: str | None = None
