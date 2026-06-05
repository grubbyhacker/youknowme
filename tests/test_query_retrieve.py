from __future__ import annotations

from pathlib import Path

from ykm.build import build_index
from ykm.contracts import QueryRequest, RetrieveRequest
from ykm.embeddings import FakeEmbeddingProvider
from ykm.index import YkmIndex


def built_index(tmp_path: Path) -> YkmIndex:
    out = tmp_path / "index"
    provider = FakeEmbeddingProvider()
    build_index(Path("fixtures/corpus"), out, provider)
    return YkmIndex(out, provider)


def test_query_returns_distinct_ambiguous_subjects(tmp_path: Path) -> None:
    index = built_index(tmp_path)

    response = index.query(QueryRequest(query="weekly spa maintenance", tags=["spa"], limit=5))

    source_ids = {result.source_id for result in response.results}
    assert {"spa-home", "spa-cabin"}.issubset(source_ids)
    assert all(result.retrieve_pointer.section_id for result in response.results)


def test_query_filter_prevents_wrong_subject(tmp_path: Path) -> None:
    index = built_index(tmp_path)

    response = index.query(QueryRequest(query="weekly maintenance", tags=["bromine"], limit=5))

    assert response.results
    assert {result.source_id for result in response.results} == {"spa-cabin"}
    assert "chlorine granules" not in response.results[0].returned_content


def test_related_links_are_visible_not_traversed(tmp_path: Path) -> None:
    index = built_index(tmp_path)

    response = index.query(QueryRequest(query="home chlorine weekly", tags=["home"], limit=1))

    assert response.results[0].related == ["chemical-storage"]
    assert "chemical-storage" not in {result.source_id for result in response.results}


def test_retrieve_by_section_is_deterministic(tmp_path: Path) -> None:
    index = built_index(tmp_path)
    query = index.query(QueryRequest(query="home chlorine weekly", tags=["home"], limit=1))
    section_id = query.results[0].section_id

    response = index.retrieve(RetrieveRequest(locator=section_id, kind="section_id"))

    assert response.found is True
    assert response.section_id == section_id
    assert "Weekly maintenance" in response.content


def test_retrieve_by_alias_and_explicit_id_survives_rename(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    renamed_dir = corpus / "renamed"
    renamed_dir.mkdir(parents=True)
    source = Path("fixtures/corpus/procedures/home-spa.md").read_text(encoding="utf-8")
    (renamed_dir / "moved-spa.md").write_text(source, encoding="utf-8")
    index_path = tmp_path / "index"
    provider = FakeEmbeddingProvider()
    build_index(corpus, index_path, provider)
    index = YkmIndex(index_path, provider)

    by_id = index.retrieve(RetrieveRequest(locator="spa-home", kind="source_id"))
    by_alias = index.retrieve(RetrieveRequest(locator="old-home-spa", kind="source_id"))

    assert by_id.found is True
    assert by_alias.found is True
    assert by_id.source_id == by_alias.source_id == "spa-home"


def test_unknown_retrieve_is_not_found(tmp_path: Path) -> None:
    index = built_index(tmp_path)

    response = index.retrieve(RetrieveRequest(locator="missing", kind="source_id"))

    assert response.found is False
    assert response.content is None


def test_no_results_is_success(tmp_path: Path) -> None:
    index = built_index(tmp_path)

    response = index.query(QueryRequest(query="anything", tags=["does-not-exist"]))

    assert response.results == []
