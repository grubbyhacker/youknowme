from __future__ import annotations

import os
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


CF_ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
LOCAL_AUTH_HEADER = "X-YKM-Local-Secret"


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    owner_email: str = ""
    cloudflare_team_domain: str = ""
    cloudflare_aud: str = ""
    local_secret: str = ""

    @classmethod
    def from_env(cls, mode: str) -> AuthConfig:
        return cls(
            mode=mode,
            owner_email=os.getenv("YKM_OWNER_EMAIL", ""),
            cloudflare_team_domain=os.getenv("YKM_CLOUDFLARE_TEAM_DOMAIN", "").rstrip("/"),
            cloudflare_aud=os.getenv("YKM_CLOUDFLARE_AUD", ""),
            local_secret=os.getenv("YKM_LOCAL_AUTH_SECRET", ""),
        )


class AuthVerifier:
    def __init__(self, config: AuthConfig, jwk_client: PyJWKClient | None = None) -> None:
        self.config = config
        self._jwk_client = jwk_client

    def verify_request(self, request: Request) -> tuple[bool, str]:
        if request.url.path == "/livez":
            return True, "liveness"
        if self.config.mode == "local":
            return self._verify_local(request)
        if self.config.mode == "public":
            return self._verify_cloudflare(request)
        return False, "unknown auth mode"

    def _verify_local(self, request: Request) -> tuple[bool, str]:
        if not self.config.local_secret:
            return False, "missing local secret config"
        if request.headers.get(LOCAL_AUTH_HEADER) != self.config.local_secret:
            return False, "invalid local secret"
        return True, "local"

    def _verify_cloudflare(self, request: Request) -> tuple[bool, str]:
        token = request.headers.get(CF_ACCESS_JWT_HEADER)
        if not token:
            return False, "missing Cloudflare Access JWT"
        try:
            claims = self.decode_cloudflare_jwt(token)
        except (PyJWTError, RuntimeError, OSError) as exc:
            return False, f"invalid Cloudflare Access JWT: {exc}"
        email = str(claims.get("email", "")).lower()
        if not self.config.owner_email or email != self.config.owner_email.lower():
            return False, "owner email mismatch"
        return True, "cloudflare"

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
        ok, reason = self.verifier.verify_request(request)
        if not ok:
            return JSONResponse({"detail": "forbidden", "reason": reason}, status_code=403)
        request.state.auth_path = reason
        return await call_next(request)

