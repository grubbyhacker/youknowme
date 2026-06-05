from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import lancedb

from ykm.build import preview_parent
from ykm.contracts import (
    ArtifactManifest,
    ChunkRecord,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    QueryResult,
    RetrieveRequest,
    RetrieveResponse,
    SourcePointer,
)
from ykm.embeddings import EmbeddingProvider, cosine_similarity


class YkmIndex:
    def __init__(self, path: Path, provider: EmbeddingProvider) -> None:
        self.path = path.resolve()
        self.provider = provider
        self.manifest = ArtifactManifest.model_validate_json(
            (self.path / "manifest.json").read_text(encoding="utf-8")
        )
        self.chunks = [
            ChunkRecord.model_validate_json(line)
            for line in (self.path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self._db = lancedb.connect(self.path / "lancedb")
        self._table = self._db.open_table("chunks")

    def query(self, request: QueryRequest) -> QueryResponse:
        if not self.chunks:
            return QueryResponse(
                results=[],
                build_id=self.manifest.build_id,
                source_commit=self.manifest.source_commit,
            )

        query_vector = self.provider.embed([request.query])[0]
        candidates = self._filtered_chunks(request)
        scored = [
            (cosine_similarity(query_vector, self._vector_for(chunk.chunk_id)), chunk)
            for chunk in candidates
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[QueryResult] = []
        seen_sections: set[str] = set()
        for score, chunk in scored:
            if chunk.section_id in seen_sections:
                continue
            seen_sections.add(chunk.section_id)
            returned_content, truncated = preview_parent(chunk.parent_text, chunk.text)
            result_id = f"{chunk.chunk_id}:{len(results)}"
            results.append(
                QueryResult(
                    result_id=result_id,
                    source_id=chunk.source_id,
                    source_path=chunk.source_path,
                    section_id=chunk.section_id,
                    parent_section=chunk.section_heading,
                    matched_chunk=chunk.text,
                    returned_content=returned_content,
                    tags=chunk.tags,
                    type=chunk.type,
                    score=round(float(score), 6),
                    disambiguation_hint=disambiguation_hint(chunk),
                    related=chunk.related,
                    truncated=truncated,
                    retrieve_pointer=SourcePointer(
                        source_id=chunk.source_id,
                        source_path=chunk.source_path,
                        section_id=chunk.section_id,
                    ),
                )
            )
            if len(results) >= request.limit:
                break
        return QueryResponse(
            results=results,
            build_id=self.manifest.build_id,
            source_commit=self.manifest.source_commit,
        )

    def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        matches = list(self._retrieve_matches(request))
        if not matches:
            return RetrieveResponse(
                found=False,
                locator=request.locator,
                kind=request.kind,
                build_id=self.manifest.build_id,
            )
        chunk = matches[0]
        content = chunk.parent_text if request.kind == "section_id" else self._source_text(chunk)
        return RetrieveResponse(
            found=True,
            locator=request.locator,
            kind=request.kind,
            source_id=chunk.source_id,
            source_path=chunk.source_path,
            section_id=chunk.section_id if request.kind == "section_id" else None,
            content=content,
            build_id=self.manifest.build_id,
        )

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            index_loaded=True,
            source_commit=self.manifest.source_commit,
            build_id=self.manifest.build_id,
            embedding_model=self.manifest.embedding_model,
            created_at=self.manifest.created_at,
        )

    def explain(self, result_id: str) -> dict[str, object] | None:
        chunk_id = result_id.split(":", 1)[0]
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return {
                    "chunk_id": chunk.chunk_id,
                    "source_id": chunk.source_id,
                    "source_path": chunk.source_path,
                    "section_id": chunk.section_id,
                    "section_heading": chunk.section_heading,
                    "tags": chunk.tags,
                    "type": chunk.type,
                    "matched_text": chunk.text,
                    "parent_chars": len(chunk.parent_text),
                }
        return None

    def _filtered_chunks(self, request: QueryRequest) -> list[ChunkRecord]:
        tags = {tag.lower() for tag in request.tags}
        tags_any = {tag.lower() for tag in request.tags_any}
        output: list[ChunkRecord] = []
        for chunk in self.chunks:
            if request.type and chunk.type != request.type.lower():
                continue
            if request.source and request.source not in chunk.source_path:
                continue
            chunk_tags = set(chunk.tags)
            if tags and not tags.issubset(chunk_tags):
                continue
            if tags_any and not tags_any.intersection(chunk_tags):
                continue
            output.append(chunk)
        return output

    def _retrieve_matches(self, request: RetrieveRequest) -> Iterable[ChunkRecord]:
        if request.kind == "source_id":
            yielded: set[str] = set()
            for chunk in self.chunks:
                if (
                    request.locator == chunk.source_id or request.locator in chunk.aliases
                ) and chunk.source_id not in yielded:
                    yielded.add(chunk.source_id)
                    yield chunk
        elif request.kind == "section_id":
            for chunk in self.chunks:
                if chunk.section_id == request.locator:
                    yield chunk
                    return
        elif request.kind == "path":
            yielded_paths: set[str] = set()
            for chunk in self.chunks:
                if chunk.source_path == request.locator and chunk.source_path not in yielded_paths:
                    yielded_paths.add(chunk.source_path)
                    yield chunk

    def _source_text(self, chunk: ChunkRecord) -> str:
        sections = [
            item.parent_text
            for item in self.chunks
            if item.source_id == chunk.source_id and item.ordinal == 0
        ]
        return "\n\n".join(dict.fromkeys(sections))

    def _vector_for(self, chunk_id: str) -> list[float]:
        rows = self._table.search().where(f"chunk_id = '{chunk_id}'").limit(1).to_list()
        if not rows:
            return []
        vector = rows[0]["vector"]
        return json.loads(vector) if isinstance(vector, str) else vector


def disambiguation_hint(chunk: ChunkRecord) -> str:
    pieces = [chunk.type, *chunk.tags, chunk.source_path]
    return " | ".join(piece for piece in pieces if piece)
