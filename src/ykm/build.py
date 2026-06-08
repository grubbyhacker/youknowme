from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import lancedb
from markdown_it import MarkdownIt

from ykm.contracts import (
    ArtifactManifest,
    BuildOutput,
    BuildWarning,
    ChunkRecord,
    QuarantineRecord,
    SourceDoc,
)
from ykm.embeddings import EmbeddingProvider


MAX_PARENT_CHARS = 1800
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}"),
]


def build_index(
    corpus: Path,
    out: Path,
    provider: EmbeddingProvider,
    *,
    include_roots: list[str] | None = None,
) -> BuildOutput:
    corpus = corpus.resolve()
    out = out.resolve()
    if not corpus.exists():
        raise FileNotFoundError(f"Corpus path does not exist: {corpus}")

    docs, warnings, quarantined = load_corpus(corpus, include_roots=include_roots)
    chunks: list[ChunkRecord] = []
    for doc in docs:
        doc_chunks, doc_warnings = chunk_document(doc)
        chunks.extend(doc_chunks)
        warnings.extend(doc_warnings)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    vectors = provider.embed([chunk.text for chunk in chunks])
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append({**chunk.model_dump(), "vector": vector})

    db = lancedb.connect(out / "lancedb")
    if rows:
        db.create_table("chunks", data=rows, mode="overwrite")
    else:
        db.create_table("chunks", data=[_empty_row(provider.dimensions)], mode="overwrite")

    manifest = ArtifactManifest(
        build_id=uuid4().hex,
        source_commit=source_commit(corpus),
        embedding_provider=provider.name,
        embedding_model=provider.model,
        embedding_dimensions=provider.dimensions,
        created_at=datetime.now(UTC),
        chunk_count=len(chunks),
        quarantined_count=len(quarantined),
        warning_count=len(warnings),
    )
    write_json(out / "manifest.json", manifest.model_dump(mode="json"))
    write_jsonl(out / "chunks.jsonl", [chunk.model_dump(mode="json") for chunk in chunks])
    write_jsonl(out / "warnings.jsonl", [warning.model_dump(mode="json") for warning in warnings])
    write_jsonl(
        out / "quarantine.jsonl",
        [record.model_dump(mode="json") for record in quarantined],
    )
    return BuildOutput(manifest=manifest, warnings=warnings, quarantined=quarantined)


def load_corpus(
    corpus: Path,
    *,
    include_roots: list[str] | None = None,
) -> tuple[list[SourceDoc], list[BuildWarning], list[QuarantineRecord]]:
    docs: list[SourceDoc] = []
    warnings: list[BuildWarning] = []
    quarantined: list[QuarantineRecord] = []
    for path in markdown_paths(corpus, include_roots):
        rel_path = path.relative_to(corpus).as_posix()
        text = path.read_text(encoding="utf-8")
        secret_reason = detect_secret(text)
        if secret_reason:
            quarantined.append(QuarantineRecord(source_path=rel_path, reason=secret_reason))
            continue

        metadata, body = parse_frontmatter(text)
        title = infer_title(body, rel_path)
        source_id = str(metadata.get("id") or generated_source_id(body))
        if "id" not in metadata:
            warnings.append(
                BuildWarning(
                    source_path=rel_path,
                    code="generated-id",
                    message="No frontmatter id found; generated a content-fingerprint id.",
                )
            )
        docs.append(
            SourceDoc(
                source_id=source_id,
                aliases=normalize_list(metadata.get("aliases")),
                source_path=rel_path,
                title=title,
                type=normalize_scalar(metadata.get("type")) or infer_type(rel_path),
                tags=normalize_list(metadata.get("tags")),
                related=normalize_list(metadata.get("related")),
                body=body.strip(),
            )
        )
    return docs, warnings, quarantined


def markdown_paths(corpus: Path, include_roots: list[str] | None = None) -> list[Path]:
    if not include_roots:
        return sorted(corpus.rglob("*.md"))

    paths: list[Path] = []
    seen: set[Path] = set()
    for root_name in include_roots:
        if not root_name or root_name.startswith("/") or ".." in Path(root_name).parts:
            raise ValueError(f"include root must be a relative path inside the corpus: {root_name}")
        root = corpus / root_name
        if not root.exists():
            raise FileNotFoundError(f"include root does not exist: {root_name}")
        if root.is_file():
            candidates = [root] if root.suffix.lower() == ".md" else []
        else:
            candidates = sorted(root.rglob("*.md"))
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(candidate)
    return sorted(paths)


def chunk_document(doc: SourceDoc) -> tuple[list[ChunkRecord], list[BuildWarning]]:
    sections = split_sections(doc.body, doc.title)
    warnings: list[BuildWarning] = []
    if len(sections) == 1 and sections[0]["heading"] == doc.title:
        warnings.append(
            BuildWarning(
                source_path=doc.source_path,
                code="headerless-or-single-section",
                message="Document has no markdown section structure or only one section.",
            )
        )

    chunks: list[ChunkRecord] = []
    for section_index, section in enumerate(sections):
        parent_text = section["text"].strip()
        if len(parent_text) > MAX_PARENT_CHARS:
            warnings.append(
                BuildWarning(
                    source_path=doc.source_path,
                    code="oversized-parent",
                    message=f"Section exceeds {MAX_PARENT_CHARS} characters; query returns preview.",
                )
            )
        child_texts = split_child_chunks(parent_text)
        section_id = stable_id(doc.source_id, "section", str(section_index), section["heading"])
        for child_index, child_text in enumerate(child_texts):
            chunk_id = stable_id(section_id, "chunk", str(child_index), child_text[:80])
            chunks.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    source_id=doc.source_id,
                    aliases=doc.aliases,
                    source_path=doc.source_path,
                    section_id=section_id,
                    section_heading=section["heading"],
                    heading_path=section["heading_path"],
                    type=doc.type,
                    tags=doc.tags,
                    related=doc.related,
                    text=child_text.strip(),
                    parent_text=parent_text,
                    ordinal=child_index,
                )
            )
    return chunks, warnings


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip()
    body = text[text.find("\n", end + 1) + 1 :]
    metadata: dict[str, object] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            metadata.setdefault(current_key, [])
            value = line[4:].strip()
            assert isinstance(metadata[current_key], list)
            metadata[current_key].append(value)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        current_key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            metadata[current_key] = [
                item.strip().strip("\"'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        elif value:
            metadata[current_key] = value.strip("\"'")
        else:
            metadata[current_key] = []
    return metadata, body


def split_sections(body: str, fallback_title: str) -> list[dict[str, object]]:
    md = MarkdownIt()
    tokens = md.parse(body)
    heading_lines: list[tuple[int, int, str, int]] = []
    lines = body.splitlines()
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or not token.map:
            continue
        level = int(token.tag[1])
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        title = inline.content if inline and inline.type == "inline" else fallback_title
        heading_lines.append((token.map[0], token.map[1], title, level))

    if not heading_lines:
        return [{"heading": fallback_title, "heading_path": [fallback_title], "text": body}]

    sections: list[dict[str, object]] = []
    stack: list[tuple[int, str]] = []
    for i, (start, heading_end, title, level) in enumerate(heading_lines):
        next_start = heading_lines[i + 1][0] if i + 1 < len(heading_lines) else len(lines)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        section_text = "\n".join(lines[start:next_start]).strip()
        content_lines = [line for line in lines[heading_end:next_start] if line.strip()]
        if not content_lines:
            continue
        sections.append(
            {
                "heading": title,
                "heading_path": [item[1] for item in stack],
                "text": section_text,
                "heading_end": heading_end,
            }
        )
    return sections


def split_child_chunks(parent_text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", parent_text) if block.strip()]
    if not blocks:
        return [parent_text]
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= 700:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block
    if current:
        chunks.append(current)
    return chunks


def preview_parent(parent: str, matched: str, max_chars: int = MAX_PARENT_CHARS) -> tuple[str, bool]:
    if len(parent) <= max_chars:
        return parent, False
    index = max(parent.find(matched), 0)
    start = max(index - max_chars // 3, 0)
    end = min(start + max_chars, len(parent))
    start = max(end - max_chars, 0)
    prefix = "[truncated]\n" if start else ""
    suffix = "\n[truncated]" if end < len(parent) else ""
    return f"{prefix}{parent[start:end]}{suffix}", True


def detect_secret(text: str) -> str | None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return f"matched high-confidence secret pattern: {pattern.pattern}"
    return None


def normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return sorted({normalize_scalar(item) for item in value if normalize_scalar(item)})
    if isinstance(value, str):
        return sorted({normalize_scalar(item) for item in value.split(",") if normalize_scalar(item)})
    return []


def normalize_scalar(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def infer_title(body: str, rel_path: str) -> str:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return Path(rel_path).stem.replace("-", " ").title()


def infer_type(rel_path: str) -> str:
    first = rel_path.split("/", 1)[0].lower()
    return re.sub(r"[^a-z0-9_-]+", "-", first) if first else "note"


def generated_source_id(body: str) -> str:
    return "doc-" + hashlib.sha256(body.strip().encode("utf-8")).hexdigest()[:16]


def stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"id-{digest}"


def source_commit(corpus: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(corpus), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(corpus), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            return f"{commit}+dirty.{corpus_digest(corpus)}"
        return commit
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"local-{corpus_digest(corpus)}"


def corpus_digest(corpus: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(corpus.rglob("*.md")):
        digest.update(path.relative_to(corpus).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _empty_row(dimensions: int) -> dict[str, object]:
    return {
        "chunk_id": "__empty__",
        "source_id": "__empty__",
        "aliases": [],
        "source_path": "__empty__",
        "section_id": "__empty__",
        "section_heading": "__empty__",
        "heading_path": [],
        "type": "empty",
        "tags": [],
        "related": [],
        "text": "",
        "parent_text": "",
        "ordinal": 0,
        "start_line": None,
        "end_line": None,
        "vector": [0.0] * dimensions,
    }
