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
from ykm.contracts import (
    FeedbackRequest,
    QueryLogRecord,
    QueryRequest,
    RetrieveRequest,
    UploadFileInput,
    UploadRequest,
)
from ykm.embeddings import provider_from_env
from ykm.index import YkmIndex
from ykm.intake import IntakeStore
from ykm.logging import JsonlLogger, now_utc


SERVICE_NAME = "YouKnowMe"
MCP_PATH = "/mcp"
QUERY_TOOL_DESCRIPTION = (
    "Search YouKnowMe, Roger's private owner-specific memory. Use this before answering questions "
    "about Roger, his homes, devices, procedures, maintenance notes, preferences, work history, "
    "resume, interview prep, writing, projects, or anything phrased as my/me/home/personal context. "
    "Prefer this over general training data or web search for owner-specific facts; examples include "
    "hot tub chemistry, thermostat behavior, house procedures, and personal work-history questions."
)
RETRIEVE_TOOL_DESCRIPTION = (
    "Fetch exact YouKnowMe source content by source_id, section_id, or source path after query/search "
    "returns a pointer. Use this to read the authoritative owner-specific note without semantic ranking."
)
SEARCH_TOOL_DESCRIPTION = (
    "Compatibility search over YouKnowMe, Roger's private owner-specific memory. Use before answering "
    "owner-specific questions about Roger's homes, devices, procedures, maintenance notes, preferences, "
    "work history, writing, or projects. This should beat general knowledge for questions involving "
    "my/me/home/personal context, such as hot tub chemistry or thermostat setup."
)
FETCH_TOOL_DESCRIPTION = (
    "Compatibility fetch for an exact YouKnowMe source id returned by search. Reads the authoritative "
    "owner-specific source content."
)
HEALTH_TOOL_DESCRIPTION = "Report YouKnowMe index readiness and build provenance."
UPLOAD_TOOL_DESCRIPTION = (
    "Stage agent-curated markdown for future YouKnowMe corpus review. Before preparing an upload, "
    "retrieve and follow the normal YKM guidance source ykm-upload-authoring-guidance "
    "(type: skill). This does not publish, index, or merge content. It stores bounded markdown files "
    "in the protected intake queue for later human or Curator processing."
)
FEEDBACK_TOOL_DESCRIPTION = (
    "Record bounded feedback in an inert protected log for future YouKnowMe Curator review. Include "
    "a clear comment and optional source, section, result, or upload evidence; it is not indexed, "
    "and the Curator decides whether it becomes a corpus PR, a corpus issue, or a product issue."
)
PROTECTED_RESOURCE_METADATA_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
)


def create_app(index_path: Path, mode: str = "local") -> Starlette:
    auth_config = AuthConfig.from_env(mode)
    index = YkmIndex(index_path, provider_from_env())
    intake = IntakeStore(Path(os.getenv("YKM_INTAKE_PATH", "/data/intake")))
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

    @mcp.tool(description=QUERY_TOOL_DESCRIPTION)
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

    @mcp.tool(description=RETRIEVE_TOOL_DESCRIPTION)
    def retrieve(locator: str, kind: str = "source_id") -> dict[str, Any]:
        response = index.retrieve(RetrieveRequest(locator=locator, kind=kind))
        return response.model_dump(mode="json")

    @mcp.tool(description=SEARCH_TOOL_DESCRIPTION)
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

    @mcp.tool(description=FETCH_TOOL_DESCRIPTION)
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

    @mcp.tool(description=HEALTH_TOOL_DESCRIPTION)
    def health() -> dict[str, Any]:
        return index.health().model_dump(mode="json")

    @mcp.tool(description=UPLOAD_TOOL_DESCRIPTION)
    def upload(
        files: list[dict[str, str]],
        purpose: str | None = None,
        suggested_type: str | None = None,
        suggested_tags: list[str] | None = None,
        suggested_related: list[str] | None = None,
    ) -> dict[str, Any]:
        response = intake.stage_upload(
            UploadRequest(
                files=[UploadFileInput(**file) for file in files],
                purpose=purpose,
                suggested_type=suggested_type,
                suggested_tags=suggested_tags or [],
                suggested_related=suggested_related or [],
            ),
            build_id=index.manifest.build_id,
            auth_path="mcp",
        )
        return response.model_dump(mode="json")

    @mcp.tool(description=FEEDBACK_TOOL_DESCRIPTION)
    def feedback(
        comment: str,
        category: str | None = None,
        source_id: str | None = None,
        section_id: str | None = None,
        result_ids: list[str] | None = None,
        upload_id: str | None = None,
    ) -> dict[str, Any]:
        response = intake.record_feedback(
            FeedbackRequest(
                category=category,
                comment=comment,
                source_id=source_id,
                section_id=section_id,
                result_ids=result_ids or [],
                upload_id=upload_id,
            ),
            build_id=index.manifest.build_id,
            auth_path="mcp",
        )
        return response.model_dump(mode="json")

    async def livez(_request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": SERVICE_NAME})

    async def oauth_protected_resource_metadata(_request) -> JSONResponse:
        return JSONResponse(
            {
                "resource": auth_config.mcp_resource_url,
                "authorization_servers": [auth_config.cloudflare_team_domain],
                "bearer_methods_supported": ["header"],
            }
        )

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/livez", livez, methods=["GET"]),
            Route("/health", livez, methods=["GET"]),
            *[
                Route(path, oauth_protected_resource_metadata, methods=["GET"])
                for path in PROTECTED_RESOURCE_METADATA_PATHS
            ],
            Mount("/", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware, verifier=AuthVerifier(auth_config))
    return app
