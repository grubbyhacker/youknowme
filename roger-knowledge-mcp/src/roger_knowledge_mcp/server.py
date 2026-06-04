from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.responses import Response
from starlette.routing import Mount, Route


SERVICE_NAME = "roger-knowledge-mcp"
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
PUBLIC_HOSTNAME = os.getenv("PUBLIC_HOSTNAME", "mcp.fleiglabs.cc")
ORIGIN_HOSTNAME = os.getenv("ORIGIN_HOSTNAME", "origin-mcp.fleiglabs.cc")
CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
CF_ACCESS_TEAM_DOMAIN = os.getenv("CLOUDFLARE_ACCESS_TEAM_DOMAIN", "").rstrip("/")
CF_ACCESS_AUD = os.getenv("CLOUDFLARE_ACCESS_AUD", "")
REQUIRE_CLOUDFLARE_ACCESS_JWT = (
    os.getenv("REQUIRE_CLOUDFLARE_ACCESS_JWT", "").lower() == "true"
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(SERVICE_NAME)
cloudflare_jwk_client: PyJWKClient | None = None

mcp = FastMCP(
    SERVICE_NAME,
    streamable_http_path=MCP_PATH,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            PUBLIC_HOSTNAME,
            f"{PUBLIC_HOSTNAME}:*",
            ORIGIN_HOSTNAME,
            f"{ORIGIN_HOSTNAME}:*",
            "roger-knowledge-mcp",
            "roger-knowledge-mcp:*",
            "roger-knowledge-mcp-phase0",
            "roger-knowledge-mcp-phase0:*",
            "127.0.0.1:*",
            "localhost:*",
        ],
        allowed_origins=[
            f"https://{PUBLIC_HOSTNAME}",
            f"https://{ORIGIN_HOSTNAME}",
        ],
    ),
)

PHASE0_SEARCH_RESULT = {
    "id": "phase0:hermes",
    "title": "Hermes Phase 0 Test Note",
    "url": "roger-knowledge://phase0/hermes",
    "text": (
        "Hermes is Roger's private agent environment. This is a static Phase 0 "
        "result proving MCP search works."
    ),
}

PHASE0_DOCUMENT = {
    "id": "phase0:hermes",
    "title": "Hermes Phase 0 Test Note",
    "text": (
        "This document proves that ChatGPT can call a private MCP server through "
        "Cloudflare Tunnel and Cloudflare Access."
    ),
    "url": "roger-knowledge://phase0/hermes",
    "metadata": {
        "collection": "phase0",
        "tags": ["mcp", "chatgpt", "cloudflare", "tunnel", "poc"],
    },
}


def _is_mcp_path(path: str) -> bool:
    return path == MCP_PATH or path.startswith(f"{MCP_PATH}/")


def _cloudflare_jwks_url() -> str:
    return f"{CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs"


def _cloudflare_jwk_client() -> PyJWKClient:
    global cloudflare_jwk_client

    if cloudflare_jwk_client is None:
        cloudflare_jwk_client = PyJWKClient(_cloudflare_jwks_url())

    return cloudflare_jwk_client


def _verify_cloudflare_access_jwt(token: str) -> None:
    if not CF_ACCESS_TEAM_DOMAIN or not CF_ACCESS_AUD:
        raise RuntimeError(
            "CLOUDFLARE_ACCESS_TEAM_DOMAIN and CLOUDFLARE_ACCESS_AUD must be set"
        )

    signing_key = _cloudflare_jwk_client().get_signing_key_from_jwt(token)
    jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=CF_ACCESS_AUD,
        issuer=CF_ACCESS_TEAM_DOMAIN,
        leeway=60,
    )


class CloudflareAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not REQUIRE_CLOUDFLARE_ACCESS_JWT or not _is_mcp_path(request.url.path):
            return await call_next(request)

        token = request.headers.get(CF_ACCESS_JWT_HEADER)
        if not token:
            logger.warning("rejected missing Cloudflare Access JWT path=%s", request.url.path)
            return JSONResponse({"detail": "forbidden"}, status_code=403)

        try:
            _verify_cloudflare_access_jwt(token)
        except (PyJWTError, RuntimeError, OSError) as exc:
            logger.warning("rejected invalid Cloudflare Access JWT: %s", exc)
            return JSONResponse({"detail": "forbidden"}, status_code=403)

        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else "unknown"
        jwt_present = bool(request.headers.get(CF_ACCESS_JWT_HEADER))
        logger.info(
            "http request method=%s path=%s host=%r client=%s user_agent=%r cf_ray=%r jwt_present=%s",
            request.method,
            request.url.path,
            request.headers.get("host"),
            client,
            request.headers.get("user-agent"),
            request.headers.get("cf-ray"),
            jwt_present,
        )

        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception(
                "http request failed method=%s path=%s host=%r client=%s",
                request.method,
                request.url.path,
                request.headers.get("host"),
                client,
            )
            raise

        logger.info(
            "http response method=%s path=%s status=%s client=%s",
            request.method,
            request.url.path,
            response.status_code,
            client,
        )
        return response


def _log_tool_call(tool_name: str, arguments: dict[str, Any]) -> None:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    encoded_args = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    logger.info("%s tool=%s args=%s", timestamp, tool_name, encoded_args)


@mcp.tool()
def search(query: str) -> list[dict[str, Any]]:
    """Return static Phase 0 search results."""
    logger.info("search called query=%r", query)
    try:
        _log_tool_call("search", {"query": query})
        return [PHASE0_SEARCH_RESULT]
    except Exception:
        logger.exception("search failed query=%r", query)
        raise


@mcp.tool()
def fetch(id: str) -> dict[str, Any]:
    """Return a static Phase 0 document by id."""
    logger.info("fetch called id=%r", id)
    try:
        _log_tool_call("fetch", {"id": id})
        if id == PHASE0_DOCUMENT["id"]:
            return PHASE0_DOCUMENT

        return {
            "id": id,
            "title": "Not found",
            "text": f"No static Phase 0 document exists for id: {id}",
            "url": None,
            "metadata": {"collection": "phase0", "found": False},
        }
    except Exception:
        logger.exception("fetch failed id=%r", id)
        raise


@mcp.tool()
def health() -> dict[str, Any]:
    """Return MCP server health metadata."""
    logger.info("health called")
    try:
        _log_tool_call("health", {})
        return _health_payload()
    except Exception:
        logger.exception("health tool failed")
        raise


def _health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "transport": "streamable-http",
        "mcp_path": MCP_PATH,
    }


async def http_health(_request) -> JSONResponse:
    logger.info("health called")
    return JSONResponse(_health_payload())


@asynccontextmanager
async def lifespan(_app):
    logger.info(
        "server starting service=%s mcp_path=%s public_hostname=%s origin_hostname=%s "
        "require_cloudflare_access_jwt=%s",
        SERVICE_NAME,
        MCP_PATH,
        PUBLIC_HOSTNAME,
        ORIGIN_HOSTNAME,
        REQUIRE_CLOUDFLARE_ACCESS_JWT,
    )
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", http_health, methods=["GET"]),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)
app.add_middleware(CloudflareAccessMiddleware)
app.add_middleware(RequestLoggingMiddleware)
