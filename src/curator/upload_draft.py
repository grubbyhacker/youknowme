from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ykm.build import parse_frontmatter


ALLOWED_TYPES = {
    "interview-prep",
    "manual",
    "procedure",
    "resume",
    "skill",
    "work-history",
    "writing",
    "writing-sample",
}
ALLOWED_TAGS = {
    "5-minute",
    "90-second",
    "adrian-newey",
    "agent-guidance",
    "agentic-development",
    "ai-agents",
    "ai-coding-tools",
    "ai-engineering",
    "architectural-linting",
    "autocommenter",
    "beach-house",
    "book-review",
    "bromine",
    "bryant",
    "capital-one",
    "car-review",
    "career-summary",
    "chlorine",
    "cisco",
    "code-coverage",
    "code-health",
    "code-review",
    "core-ml",
    "corpus-authoring",
    "crusoe",
    "deep-dive",
    "developer-productivity",
    "devx",
    "financial-services",
    "follow-me",
    "formula-1",
    "google",
    "heat-pump",
    "home",
    "home-maintenance",
    "hot-tub",
    "hvac",
    "ic",
    "interview",
    "interview-prep",
    "interview-story",
    "leadership",
    "long-version",
    "maintenance",
    "markdown",
    "metrics",
    "microsoft",
    "mutation-testing",
    "narrow-pipe",
    "parallelism",
    "platform-engineering",
    "recruiter",
    "regulated-industries",
    "resume",
    "roadmaps",
    "santa-cruz",
    "security",
    "security-business-group",
    "sensenmann",
    "short-version",
    "spa",
    "substack",
    "technical-leadership",
    "thermostat",
    "token-budget",
    "tools-team",
    "troubleshooting",
    "upload",
    "vertex-ai",
    "wayfinding",
    "wired-controller",
    "work-history",
    "writing-sample",
    "youknowme",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class UploadCorpusDraftFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_filename: str
    target_path: str
    content: str
    warnings: list[str] = Field(default_factory=list)


class UploadCorpusDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["corpus_pr_candidate", "needs_owner_action"]
    files: list[UploadCorpusDraftFile] = Field(default_factory=list)
    reason: str
    warnings: list[str] = Field(default_factory=list)


def draft_upload_corpus_change(bundle_path: Path) -> UploadCorpusDraft:
    files_dir = bundle_path / "files"
    if not files_dir.exists() or not files_dir.is_dir():
        return UploadCorpusDraft(
            status="needs_owner_action",
            reason="upload bundle has no files directory",
        )
    markdown_files = sorted(path for path in files_dir.iterdir() if path.is_file() and path.suffix == ".md")
    if not markdown_files:
        return UploadCorpusDraft(
            status="needs_owner_action",
            reason="upload bundle contains no markdown files",
        )

    draft_files: list[UploadCorpusDraftFile] = []
    warnings: list[str] = []
    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return UploadCorpusDraft(
                status="needs_owner_action",
                reason=f"{path.name} is not valid UTF-8",
            )
        metadata, body = parse_frontmatter(text)
        normalized, file_warnings, blocking_reason = _normalize_metadata(metadata)
        if blocking_reason is not None:
            return UploadCorpusDraft(
                status="needs_owner_action",
                reason=f"{path.name}: {blocking_reason}",
                warnings=warnings + file_warnings,
            )
        assert normalized is not None
        target_path = _target_path(path.name, normalized)
        draft_files.append(
            UploadCorpusDraftFile(
                source_filename=path.name,
                target_path=target_path,
                content=_render_document(normalized, body),
                warnings=file_warnings,
            )
        )
        warnings.extend(f"{path.name}: {warning}" for warning in file_warnings)

    return UploadCorpusDraft(
        status="corpus_pr_candidate",
        files=draft_files,
        reason="upload markdown can be normalized into corpus source files",
        warnings=warnings,
    )


def _normalize_metadata(
    metadata: dict[str, object],
) -> tuple[dict[str, object] | None, list[str], str | None]:
    warnings: list[str] = []
    doc_id = metadata.get("id")
    if not isinstance(doc_id, str) or not ID_RE.match(doc_id):
        return None, warnings, "missing or invalid frontmatter id"
    doc_type = metadata.get("type")
    if not isinstance(doc_type, str) or doc_type not in ALLOWED_TYPES:
        return None, warnings, "missing or unsupported frontmatter type"
    raw_tags = metadata.get("tags")
    if not isinstance(raw_tags, list) or not raw_tags:
        return None, warnings, "missing or invalid frontmatter tags"
    tags = [tag for tag in _string_list(raw_tags) if tag in ALLOWED_TAGS]
    dropped_tags = sorted(set(_string_list(raw_tags)) - set(tags))
    if dropped_tags:
        warnings.append("dropped unsupported tags: " + ", ".join(dropped_tags))
    if not tags:
        return None, warnings, "frontmatter tags contain no supported corpus tags"

    normalized: dict[str, object] = {
        "id": doc_id,
        "type": doc_type,
        "tags": sorted(set(tags)),
    }
    aliases = [value for value in _string_list(metadata.get("aliases")) if ID_RE.match(value)]
    if aliases:
        normalized["aliases"] = sorted(set(aliases))
    related = [value for value in _string_list(metadata.get("related")) if ID_RE.match(value)]
    if related:
        normalized["related"] = sorted(set(related))
    return normalized, warnings, None


def _target_path(filename: str, metadata: dict[str, object]) -> str:
    doc_type = metadata["type"]
    tags = set(_string_list(metadata.get("tags")))
    if "substack" in tags:
        root = "substack"
    elif doc_type in {"resume", "work-history", "interview-prep"}:
        root = "workhistory"
    elif doc_type == "skill":
        root = "skills"
    elif doc_type in {"writing", "writing-sample"}:
        root = "writingsamples"
    elif tags & {"home-maintenance", "home", "hot-tub", "hvac", "thermostat", "beach-house"}:
        root = "homemaint"
    else:
        root = "workhistory"
    return f"{root}/{filename}"


def _render_document(metadata: dict[str, object], body: str) -> str:
    lines = ["---"]
    for key in ("id", "type", "tags", "aliases", "related"):
        if key not in metadata:
            continue
        value = metadata[key]
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", "", body.strip(), ""])
    return "\n".join(lines)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
