from __future__ import annotations

from pathlib import PurePosixPath


FORBIDDEN_BACKUP_OR_TEMP_SUFFIXES = (
    ".bak",
    ".orig",
    ".rej",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
)


def is_backup_or_temp_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name.endswith("~")
        or name.startswith(".#")
        or any(name.endswith(suffix) for suffix in FORBIDDEN_BACKUP_OR_TEMP_SUFFIXES)
    )


def forbidden_backup_or_temp_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if is_backup_or_temp_path(path)]
