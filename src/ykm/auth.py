from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
LOCAL_AUTH_HEADER = "X-YKM-Local-Secret"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    owner_email: str = ""
    cloudflare_team_domain: str = ""
    cloudflare_aud: str = ""
    allowed_service_common_names: frozenset[str] = frozenset()
    mcp_resource_url: str = "https://mcp.fleiglabs.cc/mcp"
    local_secret: str = ""

    @classmethod
    def from_env(cls, mode: str) -> AuthConfig:
        return cls(
            mode=mode,
            owner_email=os.getenv("YKM_OWNER_EMAIL", ""),
            cloudflare_team_domain=os.getenv("YKM_CLOUDFLARE_TEAM_DOMAIN", "").rstrip("/"),
            cloudflare_aud=os.getenv("YKM_CLOUDFLARE_AUD", ""),
            allowed_service_common_names=frozenset(
                value.strip()
                for value in os.getenv("YKM_ALLOWED_SERVICE_COMMON_NAMES", "").split(",")
                if value.strip()
            ),
            mcp_resource_url=os.getenv(
                "YKM_MCP_RESOURCE_URL", "https://mcp.fleiglabs.cc/mcp"
            ),
            local_secret=os.getenv("YKM_LOCAL_AUTH_SECRET", ""),
        )


@dataclass(frozen=True)
class AuthDecision:
    ok: bool
    reason: str
    status_code: int = 200


class AuthVerifier:
    def __init__(self, config: AuthConfig, jwk_client: PyJWKClient | None = None) -> None:
        self.config = config
        self._jwk_client = jwk_client

    def verify_request(self, request: Request) -> AuthDecision:
        if request.url.path in {
            "/livez",
            "/readyz",
            "/health",
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        }:
            return AuthDecision(True, "liveness")
        if self.config.mode == "local":
            return self._verify_local(request)
        if self.config.mode == "public":
            return self._verify_cloudflare(request)
        return AuthDecision(False, "unknown auth mode", 403)

    def _verify_local(self, request: Request) -> AuthDecision:
        if not self.config.local_secret:
            return AuthDecision(False, "missing local secret config", 403)
        if request.headers.get(LOCAL_AUTH_HEADER) != self.config.local_secret:
            return AuthDecision(False, "invalid local secret", 403)
        return AuthDecision(True, "local")

    def _verify_cloudflare(self, request: Request) -> AuthDecision:
        token = self._request_token(request)
        if not token:
            return AuthDecision(False, "missing Cloudflare Access token", 401)
        try:
            claims = self.decode_cloudflare_jwt(token)
        except (PyJWTError, RuntimeError, OSError) as exc:
            return AuthDecision(False, f"invalid Cloudflare Access token: {exc}", 401)
        email = str(claims.get("email", "")).lower()
        if self.config.owner_email and email == self.config.owner_email.lower():
            return AuthDecision(True, "cloudflare")

        common_name = str(claims.get("common_name", "")).strip()
        if common_name and common_name in self.config.allowed_service_common_names:
            return AuthDecision(True, "cloudflare-service")

        if common_name or not email:
            return AuthDecision(False, "owner email or service identity mismatch", 403)
        return AuthDecision(False, "owner email mismatch", 403)

    def _request_token(self, request: Request) -> str:
        access_jwt = request.headers.get(CF_ACCESS_JWT_HEADER, "").strip()
        if access_jwt:
            return access_jwt

        authorization = request.headers.get("Authorization", "").strip()
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
        return ""

    def decode_cloudflare_jwt(self, token: str) -> dict[str, object]:
        if not (
            self.config.cloudflare_team_domain
            and self.config.cloudflare_aud
            and self.config.owner_email
        ):
            raise RuntimeError("Cloudflare auth config is incomplete")
        jwk_client = self._jwk_client or PyJWKClient(
            f"{self.config.cloudflare_team_domain}/cdn-cgi/access/certs"
        )
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.config.cloudflare_aud,
            issuer=self.config.cloudflare_team_domain,
            leeway=60,
        )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, verifier: AuthVerifier) -> None:
        super().__init__(app)
        self.verifier = verifier

    async def dispatch(self, request: Request, call_next):
        decision = self.verifier.verify_request(request)
        if not decision.ok:
            logger.warning(
                "auth rejected path=%s status=%s reason=%s",
                request.url.path,
                decision.status_code,
                decision.reason,
            )
            headers = {}
            if decision.status_code == 401:
                headers["WWW-Authenticate"] = self._www_authenticate()
            return JSONResponse(
                {"detail": "unauthorized" if decision.status_code == 401 else "forbidden"},
                status_code=decision.status_code,
                headers=headers,
            )
        if decision.reason != "liveness":
            logger.info("auth accepted path=%s reason=%s", request.url.path, decision.reason)
        request.state.auth_path = decision.reason
        return await call_next(request)

    def _www_authenticate(self) -> str:
        parsed = urlparse(self.verifier.config.mcp_resource_url)
        metadata_url = (
            f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"
            f"{parsed.path}"
        )
        return f'Bearer realm="YouKnowMe", resource_metadata="{metadata_url}"'
