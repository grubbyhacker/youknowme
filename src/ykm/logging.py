from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ykm.contracts import QueryLogRecord


class JsonlLogger:
    def __init__(self, path: Path | None, retention_days: int = 90) -> None:
        self.path = path
        self.retention_days = retention_days
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.prune()

    def write(self, record: QueryLogRecord) -> None:
        if not self.path:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")

    def prune(self) -> None:
        if not self.path or not self.path.exists():
            return
        cutoff = now_utc().timestamp() - (self.retention_days * 24 * 60 * 60)
        kept: list[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                timestamp = datetime.fromisoformat(json.loads(line)["timestamp"]).timestamp()
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                kept.append(line)
                continue
            if timestamp >= cutoff:
                kept.append(line)
        self.path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


def now_utc() -> datetime:
    return datetime.now(UTC)
