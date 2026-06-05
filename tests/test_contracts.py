from __future__ import annotations

from ykm.contracts import QueryRequest


def test_query_limit_is_bounded() -> None:
    request = QueryRequest(query="hello", limit=10)

    assert request.limit == 10

