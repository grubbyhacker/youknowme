from __future__ import annotations

from pathlib import Path

import subprocess

from ykm.build import build_index, load_corpus, source_commit
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


def test_load_corpus_accepts_frontmatter_block_lists(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "thermostat.md").write_text(
        """---
id: thermostat-test
type: procedure
tags:
  - hvac
  - thermostat
related:
  - house-notes
aliases:
  - old-thermostat-test
---

# Thermostat Test

Use Follow Me.
""",
        encoding="utf-8",
    )

    docs, _warnings, _quarantined = load_corpus(corpus)

    assert docs[0].tags == ["hvac", "thermostat"]
    assert docs[0].related == ["house-notes"]
    assert docs[0].aliases == ["old-thermostat-test"]


def test_source_commit_marks_dirty_git_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("# Note\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=corpus, check=True, capture_output=True)
    subprocess.run(["git", "add", "note.md"], cwd=corpus, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=YKM Test",
            "-c",
            "user.email=ykm-test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        cwd=corpus,
        check=True,
        capture_output=True,
    )

    clean = source_commit(corpus)
    (corpus / "new.md").write_text("# New\n", encoding="utf-8")
    dirty = source_commit(corpus)

    assert "+dirty." not in clean
    assert dirty.startswith(f"{clean}+dirty.")
