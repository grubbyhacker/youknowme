from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from ykm.auth import AuthConfig, AuthMiddleware, AuthVerifier
from ykm.contracts import QueryLogRecord, QueryRequest, RetrieveRequest
from ykm.embeddings import provider_from_env
from ykm.index import YkmIndex
from ykm.logging import JsonlLogger, now_utc


SERVICE_NAME = "YouKnowMe"
MCP_PATH = "/mcp"


def create_app(index_path: Path, mode: str = "local") -> Starlette:
    index = YkmIndex(index_path, provider_from_env())
    logger = JsonlLogger(
        Path(os.getenv("YKM_LOG_PATH")) if os.getenv("YKM_LOG_PATH") else None,
        int(os.getenv("YKM_LOG_RETENTION_DAYS", "90")),
    )
    mcp = FastMCP(
        SERVICE_NAME,
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "testserver",
                "testserver:*",
                "mcp.fleiglabs.cc",
                "mcp.fleiglabs.cc:*",
                "origin-mcp.fleiglabs.cc",
                "origin-mcp.fleiglabs.cc:*",
                "roger-knowledge-mcp",
                "roger-knowledge-mcp:*",
                "youknowme",
                "youknowme:*",
                "youknowme-phase1e",
                "youknowme-phase1e:*",
            ],
            allowed_origins=[],
        ),
    )

    @mcp.tool()
    def query(
        query: str,
        type: str | None = None,
        tags: list[str] | None = None,
        tags_any: list[str] | None = None,
        source: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        error = None
        response = None
        try:
            response = index.query(
                QueryRequest(
                    query=query,
                    type=type,
                    tags=tags or [],
                    tags_any=tags_any or [],
                    source=source,
                    limit=limit,
                )
            )
            return response.model_dump(mode="json")
        except Exception as exc:
            error = exc.__class__.__name__
            raise
        finally:
            logger.write(
                QueryLogRecord(
                    timestamp=now_utc(),
                    event="query",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    auth_path="mcp",
                    build_id=index.manifest.build_id,
                    result_source_ids=[
                        result.source_id for result in response.results
                    ]
                    if response
                    else [],
                    result_count=len(response.results) if response else 0,
                    error=error,
                )
            )

    @mcp.tool()
    def retrieve(locator: str, kind: str = "source_id") -> dict[str, Any]:
        response = index.retrieve(RetrieveRequest(locator=locator, kind=kind))
        return response.model_dump(mode="json")

    @mcp.tool()
    def search(query: str) -> list[dict[str, Any]]:
        started = time.perf_counter()
        error = None
        response = None
        try:
            response = index.query(QueryRequest(query=query, limit=5))
            return [
                {
                    "id": result.source_id,
                    "title": result.source_id,
                    "url": f"youknowme://{result.source_id}#{result.section_id}",
                    "text": result.returned_content,
                    "metadata": {
                        "source_id": result.source_id,
                        "source_path": result.source_path,
                        "section_id": result.section_id,
                        "tags": result.tags,
                        "type": result.type,
                        "score": result.score,
                        "retrieve_kind": "source_id",
                    },
                }
                for result in response.results
            ]
        except Exception as exc:
            error = exc.__class__.__name__
            raise
        finally:
            logger.write(
                QueryLogRecord(
                    timestamp=now_utc(),
                    event="search",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    auth_path="mcp",
                    build_id=index.manifest.build_id,
                    result_source_ids=[
                        result.source_id for result in response.results
                    ]
                    if response
                    else [],
                    result_count=len(response.results) if response else 0,
                    error=error,
                )
            )

    @mcp.tool()
    def fetch(id: str) -> dict[str, Any]:
        response = index.retrieve(RetrieveRequest(locator=id, kind="source_id"))
        if not response.found:
            return {
                "id": id,
                "title": "Not found",
                "text": f"No YouKnowMe source exists for id: {id}",
                "url": None,
                "metadata": {
                    "found": False,
                    "build_id": response.build_id,
                },
            }

        return {
            "id": response.source_id,
            "title": response.source_id,
            "text": response.content,
            "url": f"youknowme://{response.source_id}",
            "metadata": {
                "found": True,
                "source_id": response.source_id,
                "source_path": response.source_path,
                "section_id": response.section_id,
                "build_id": response.build_id,
            },
        }

    @mcp.tool()
    def health() -> dict[str, Any]:
        return index.health().model_dump(mode="json")

    async def livez(_request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": SERVICE_NAME})

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/livez", livez, methods=["GET"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware, verifier=AuthVerifier(AuthConfig.from_env(mode)))
    return app
