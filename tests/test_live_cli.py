from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import ykm.cli as cli
from ykm.live import LiveMcpConfig, LiveMcpError, live_auth_headers_from_env, live_config_from_env


def test_live_auth_headers_prefer_cloudflare_service_token() -> None:
    headers = live_auth_headers_from_env(
        {
            "YKM_CF_ACCESS_CLIENT_ID": "client-id",
            "YKM_CF_ACCESS_CLIENT_SECRET": "client-secret",
            "YKM_CF_ACCESS_JWT": "jwt",
            "YKM_LIVE_BEARER_TOKEN": "bearer",
        }
    )

    assert headers == {
        "CF-Access-Client-Id": "client-id",
        "CF-Access-Client-Secret": "client-secret",
    }


def test_live_auth_headers_reject_partial_service_token() -> None:
    with pytest.raises(LiveMcpError, match="must be set together"):
        live_auth_headers_from_env({"YKM_CF_ACCESS_CLIENT_ID": "client-id"})


def test_live_config_requires_auth_for_https() -> None:
    with pytest.raises(LiveMcpError, match="require Cloudflare Access credentials"):
        live_config_from_env(url="https://mcp.fleiglabs.cc/mcp", env={})


def test_live_config_allows_local_http_without_auth() -> None:
    config = live_config_from_env(url="http://127.0.0.1:8765/mcp", env={})

    assert config == LiveMcpConfig(url="http://127.0.0.1:8765/mcp", headers={})


def test_live_query_cli_calls_mcp_tool(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    captured: dict[str, object] = {}
    config = LiveMcpConfig(url="https://example.test/mcp", headers={"Authorization": "Bearer token"})

    def fake_config(**kwargs):
        captured["config_kwargs"] = kwargs
        return config

    def fake_call(received_config, tool_name, arguments):
        captured["config"] = received_config
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {"results": [{"source_id": "thermostat"}], "build_id": "build"}

    monkeypatch.setattr(cli, "live_config_from_env", fake_config)
    monkeypatch.setattr(cli, "call_live_tool", fake_call)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ykm",
            "live",
            "--url",
            "https://example.test/mcp",
            "--timeout",
            "12",
            "query",
            "thermostat setup",
            "--type",
            "manual",
            "--tag",
            "home",
            "--tag-any",
            "hvac",
            "--source",
            "thermostat.md",
            "--limit",
            "2",
        ],
    )

    cli.main()

    assert captured["config_kwargs"] == {
        "url": "https://example.test/mcp",
        "timeout_seconds": 12.0,
    }
    assert captured["config"] == config
    assert captured["tool_name"] == "query"
    assert captured["arguments"] == {
        "query": "thermostat setup",
        "type": "manual",
        "tags": ["home"],
        "tags_any": ["hvac"],
        "source": "thermostat.md",
        "limit": 2,
    }
    assert json.loads(capsys.readouterr().out) == {
        "build_id": "build",
        "results": [{"source_id": "thermostat"}],
    }


def test_live_tools_cli_lists_tools(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "live_config_from_env",
        lambda **_: LiveMcpConfig(url="https://example.test/mcp", headers={}),
    )
    monkeypatch.setattr(
        cli,
        "list_live_tools",
        lambda _config: [{"name": "query", "description": "Search YouKnowMe"}],
    )
    monkeypatch.setattr(sys, "argv", ["ykm", "live", "tools"])

    cli.main()

    assert json.loads(capsys.readouterr().out) == [
        {"description": "Search YouKnowMe", "name": "query"}
    ]


def test_live_upload_dry_run_does_not_call_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    markdown = tmp_path / "note.md"
    markdown.write_text("# Note\n\nBody.\n", encoding="utf-8")
    monkeypatch.setattr(cli, "call_live_tool", lambda *_: pytest.fail("unexpected MCP call"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ykm",
            "live",
            "upload",
            "--file",
            str(markdown),
            "--purpose",
            "CLI smoke",
            "--suggested-type",
            "note",
            "--suggested-tag",
            "maintenance",
            "--suggested-related",
            "other-source",
            "--dry-run",
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "arguments": {
            "files": [{"content": "# Note\n\nBody.\n", "filename": "note.md"}],
            "purpose": "CLI smoke",
            "suggested_related": ["other-source"],
            "suggested_tags": ["maintenance"],
            "suggested_type": "note",
        },
        "dry_run": True,
        "tool": "upload",
    }


def test_live_upload_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    markdown = tmp_path / "note.md"
    markdown.write_text("# Note\n", encoding="utf-8")
    monkeypatch.setattr(cli, "call_live_tool", lambda *_: pytest.fail("unexpected MCP call"))
    monkeypatch.setattr(sys, "argv", ["ykm", "live", "upload", "--file", str(markdown)])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    output = capsys.readouterr()
    assert excinfo.value.code == 2
    assert json.loads(output.out)["error"] == "confirmation_required"
    assert "rerun with --yes" in output.err


def test_live_feedback_with_yes_calls_mcp(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "live_config_from_env",
        lambda **_: LiveMcpConfig(url="https://example.test/mcp", headers={}),
    )

    def fake_call(_config, tool_name, arguments):
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {"accepted": True, "feedback_id": "fb_1", "path": "feedback/feedback.jsonl"}

    monkeypatch.setattr(cli, "call_live_tool", fake_call)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ykm",
            "live",
            "feedback",
            "--category",
            "agent_note",
            "--comment",
            "CLI smoke",
            "--source-id",
            "source",
            "--section-id",
            "section",
            "--result-id",
            "result-1",
            "--upload-id",
            "upl_1",
            "--yes",
        ],
    )

    cli.main()

    assert captured == {
        "tool_name": "feedback",
        "arguments": {
            "category": "agent_note",
            "comment": "CLI smoke",
            "result_ids": ["result-1"],
            "section_id": "section",
            "source_id": "source",
            "upload_id": "upl_1",
        },
    }
    assert json.loads(capsys.readouterr().out) == {
        "accepted": True,
        "feedback_id": "fb_1",
        "path": "feedback/feedback.jsonl",
    }
