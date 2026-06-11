from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping
from urllib.parse import urlparse

import anyio
from mcp.client.session_group import ClientSessionGroup, StreamableHttpParameters


DEFAULT_LIVE_MCP_URL = "https://mcp.fleiglabs.cc/mcp"
DEFAULT_TIMEOUT_SECONDS = 30.0


class LiveMcpError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveMcpConfig:
    url: str
    headers: dict[str, str]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def live_auth_headers_from_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    values = os.environ if env is None else env
    client_id = values.get("YKM_CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = values.get("YKM_CF_ACCESS_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
    if client_id or client_secret:
        raise LiveMcpError(
            "YKM_CF_ACCESS_CLIENT_ID and YKM_CF_ACCESS_CLIENT_SECRET must be set together"
        )

    access_jwt = values.get("YKM_CF_ACCESS_JWT", "").strip()
    if access_jwt:
        return {"Cf-Access-Jwt-Assertion": access_jwt}

    bearer_token = values.get("YKM_LIVE_BEARER_TOKEN", "").strip()
    if bearer_token:
        return {"Authorization": f"Bearer {bearer_token}"}

    local_secret = values.get("YKM_LOCAL_AUTH_SECRET", "").strip()
    if local_secret:
        return {"X-YKM-Local-Secret": local_secret}

    return {}


def live_config_from_env(
    *,
    url: str | None = None,
    timeout_seconds: float | None = None,
    env: Mapping[str, str] | None = None,
) -> LiveMcpConfig:
    values = os.environ if env is None else env
    resolved_url = (url or values.get("YKM_LIVE_MCP_URL") or DEFAULT_LIVE_MCP_URL).strip()
    resolved_timeout = timeout_seconds
    if resolved_timeout is None:
        raw_timeout = values.get("YKM_LIVE_TIMEOUT_SECONDS", "").strip()
        resolved_timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    if resolved_timeout <= 0:
        raise LiveMcpError("YKM_LIVE_TIMEOUT_SECONDS must be greater than zero")

    headers = live_auth_headers_from_env(values)
    parsed = urlparse(resolved_url)
    if parsed.scheme == "https" and not headers:
        raise LiveMcpError(
            "live HTTPS MCP calls require Cloudflare Access credentials. Set "
            "YKM_CF_ACCESS_CLIENT_ID/YKM_CF_ACCESS_CLIENT_SECRET, YKM_CF_ACCESS_JWT, "
            "or YKM_LIVE_BEARER_TOKEN."
        )
    return LiveMcpConfig(
        url=resolved_url,
        headers=headers,
        timeout_seconds=resolved_timeout,
    )


def call_live_tool(config: LiveMcpConfig, tool_name: str, arguments: dict[str, Any]) -> Any:
    return anyio.run(_call_live_tool, config, tool_name, arguments)


def list_live_tools(config: LiveMcpConfig) -> list[dict[str, Any]]:
    return anyio.run(_list_live_tools, config)


async def _call_live_tool(
    config: LiveMcpConfig, tool_name: str, arguments: dict[str, Any]
) -> Any:
    async with ClientSessionGroup() as group:
        session = await group.connect_to_server(_streamable_parameters(config))
        result = await session.call_tool(tool_name, arguments)
    return _tool_result_payload(result)


async def _list_live_tools(config: LiveMcpConfig) -> list[dict[str, Any]]:
    async with ClientSessionGroup() as group:
        session = await group.connect_to_server(_streamable_parameters(config))
        result = await session.list_tools()
    return [_tool_metadata(tool) for tool in result.tools]


def _streamable_parameters(config: LiveMcpConfig) -> StreamableHttpParameters:
    timeout = timedelta(seconds=config.timeout_seconds)
    return StreamableHttpParameters(
        url=config.url,
        headers=config.headers,
        timeout=timeout,
        sse_read_timeout=timeout,
    )


def _tool_result_payload(result: Any) -> Any:
    if getattr(result, "isError", False):
        raise LiveMcpError(_tool_result_text(result) or "MCP tool call failed")

    text = _tool_result_text(result)
    if text is None:
        return _json_safe(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"content": text}


def _tool_result_text(result: Any) -> str | None:
    content = getattr(result, "content", None)
    if not content:
        return None
    if len(content) == 1:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            return text
    return json.dumps([_json_safe(item) for item in content], sort_keys=True)


def _tool_metadata(tool: Any) -> dict[str, Any]:
    payload = {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", None),
    }
    input_schema = getattr(tool, "inputSchema", None)
    if input_schema is None:
        input_schema = getattr(tool, "input_schema", None)
    if input_schema is not None:
        payload["input_schema"] = _json_safe(input_schema)
    return payload


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
