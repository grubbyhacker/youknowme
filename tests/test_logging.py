from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from ykm.contracts import QueryLogRecord
from ykm.logging import JsonlLogger


def test_jsonl_logger_records_source_ids_without_query_text(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    logger = JsonlLogger(path)

    logger.write(
        QueryLogRecord(
            timestamp=datetime.now(UTC),
            event="query",
            latency_ms=12.5,
            auth_path="local",
            build_id="build",
            result_source_ids=["spa-home"],
            result_count=1,
        )
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["result_source_ids"] == ["spa-home"]
    assert "query" not in payload
    assert "returned_content" not in payload


def test_jsonl_logger_prunes_by_retention(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    old = QueryLogRecord(
        timestamp=datetime.now(UTC) - timedelta(days=100),
        event="query",
        latency_ms=1,
        auth_path="local",
        build_id="old",
    )
    new = QueryLogRecord(
        timestamp=datetime.now(UTC),
        event="query",
        latency_ms=1,
        auth_path="local",
        build_id="new",
    )
    path.write_text(
        old.model_dump_json() + "\n" + new.model_dump_json() + "\n",
        encoding="utf-8",
    )

    JsonlLogger(path, retention_days=90)

    assert "old" not in path.read_text(encoding="utf-8")
    assert "new" in path.read_text(encoding="utf-8")

