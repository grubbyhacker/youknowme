from __future__ import annotations

from pathlib import Path

from ykm.build import build_index, load_corpus
from ykm.embeddings import FakeEmbeddingProvider


FIXTURE_CORPUS = Path("fixtures/corpus")


def test_load_corpus_accepts_bare_markdown_and_quarantines_secret() -> None:
    docs, warnings, quarantined = load_corpus(FIXTURE_CORPUS)

    assert {doc.source_id for doc in docs} >= {"spa-home", "spa-cabin"}
    assert any(doc.source_path == "notes/headerless.md" for doc in docs)
    assert any(warning.code == "generated-id" for warning in warnings)
    assert [record.source_path for record in quarantined] == ["secrets/leaked-token.md"]


def test_build_index_writes_manifest_and_chunks(tmp_path: Path) -> None:
    output = build_index(FIXTURE_CORPUS, tmp_path / "index", FakeEmbeddingProvider())

    assert output.manifest.chunk_count > 0
    assert output.manifest.embedding_provider == "fake"
    assert (tmp_path / "index" / "manifest.json").exists()
    assert (tmp_path / "index" / "chunks.jsonl").exists()
    assert (tmp_path / "index" / "lancedb").exists()

