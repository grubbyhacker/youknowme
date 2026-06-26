#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MAX_TEST_FILE_LINES = 1000


def main() -> int:
    oversized: list[tuple[int, Path]] = []
    for path in sorted(Path("tests").glob("*.py")):
        line_count = sum(1 for _ in path.open(encoding="utf-8"))
        if line_count > MAX_TEST_FILE_LINES:
            oversized.append((line_count, path))

    if not oversized:
        return 0

    print(
        f"Test files must be {MAX_TEST_FILE_LINES} lines or fewer. "
        "Split oversized files by behavior/domain.",
        file=sys.stderr,
    )
    for line_count, path in oversized:
        print(f"{line_count:5} {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
