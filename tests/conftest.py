from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_curator_forbidden_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "YKM_GITHUB_PRIVATE_KEY_PATH",
        "YKM_CF_ACCESS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
