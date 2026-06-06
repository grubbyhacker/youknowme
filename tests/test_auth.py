from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ykm.auth import AuthConfig, AuthVerifier, CF_ACCESS_JWT_HEADER, LOCAL_AUTH_HEADER


class FakeRequest:
    def __init__(self, path: str, headers: dict[str, str]) -> None:
        self.url = SimpleNamespace(path=path)
        self.headers = headers


class StaticJwkClient:
    def __init__(self, key) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, _token: str):
        return SimpleNamespace(key=self.key.public_key())


@pytest.fixture()
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(
    private_key,
    email: str | None,
    aud: str = "aud",
    issuer: str = "https://team.cloudflareaccess.com",
    expires_delta: timedelta = timedelta(minutes=5),
    common_name: str | None = None,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": issuer,
        "aud": aud,
        "iat": now,
        "exp": now + expires_delta,
    }
    if email is not None:
        claims["email"] = email
    if common_name is not None:
        claims["common_name"] = common_name
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
    )


def public_verifier(
    private_key,
    owner_email: str = "owner@example.com",
    allowed_service_common_names: frozenset[str] = frozenset(),
) -> AuthVerifier:
    return AuthVerifier(
        AuthConfig(
            mode="public",
            owner_email=owner_email,
            cloudflare_team_domain="https://team.cloudflareaccess.com",
            cloudflare_aud="aud",
            allowed_service_common_names=allowed_service_common_names,
        ),
        StaticJwkClient(private_key),
    )


def test_public_auth_accepts_valid_owner_cloudflare_access_jwt(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest("/mcp", {CF_ACCESS_JWT_HEADER: token(private_key, "owner@example.com")})
    )

    assert decision.ok is True
    assert decision.reason == "cloudflare"


def test_public_auth_accepts_valid_owner_bearer_jwt(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest(
            "/mcp",
            {"Authorization": f"Bearer {token(private_key, 'owner@example.com')}"},
        )
    )

    assert decision.ok is True
    assert decision.reason == "cloudflare"


def test_auth_config_parses_allowed_service_common_names(monkeypatch) -> None:
    monkeypatch.setenv("YKM_ALLOWED_SERVICE_COMMON_NAMES", " hermes-client-id,,other-client-id ")

    config = AuthConfig.from_env("public")

    assert config.allowed_service_common_names == frozenset(
        {"hermes-client-id", "other-client-id"}
    )


def test_public_auth_accepts_allowed_service_token_common_name(private_key) -> None:
    verifier = public_verifier(
        private_key,
        allowed_service_common_names=frozenset({"hermes-client-id"}),
    )

    decision = verifier.verify_request(
        FakeRequest(
            "/mcp",
            {
                CF_ACCESS_JWT_HEADER: token(
                    private_key,
                    None,
                    common_name="hermes-client-id",
                )
            },
        )
    )

    assert decision.ok is True
    assert decision.reason == "cloudflare-service"


def test_public_auth_rejects_unallowed_service_token_common_name(private_key) -> None:
    verifier = public_verifier(
        private_key,
        allowed_service_common_names=frozenset({"hermes-client-id"}),
    )

    decision = verifier.verify_request(
        FakeRequest(
            "/mcp",
            {
                CF_ACCESS_JWT_HEADER: token(
                    private_key,
                    None,
                    common_name="other-client-id",
                )
            },
        )
    )

    assert decision.ok is False
    assert decision.status_code == 403
    assert decision.reason == "owner email or service identity mismatch"


def test_public_auth_rejects_token_without_email_or_service_identity(private_key) -> None:
    verifier = public_verifier(
        private_key,
        allowed_service_common_names=frozenset({"hermes-client-id"}),
    )

    decision = verifier.verify_request(
        FakeRequest("/mcp", {CF_ACCESS_JWT_HEADER: token(private_key, None)})
    )

    assert decision.ok is False
    assert decision.status_code == 403
    assert decision.reason == "owner email or service identity mismatch"


def test_public_auth_missing_token_is_401(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(FakeRequest("/mcp", {}))

    assert decision.ok is False
    assert decision.status_code == 401
    assert decision.reason == "missing Cloudflare Access token"


def test_public_auth_malformed_token_is_401(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest("/mcp", {"Authorization": "Bearer not-a-jwt"})
    )

    assert decision.ok is False
    assert decision.status_code == 401


def test_public_auth_fails_closed_on_wrong_email(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest("/mcp", {CF_ACCESS_JWT_HEADER: token(private_key, "other@example.com")})
    )

    assert decision.ok is False
    assert decision.status_code == 403
    assert decision.reason == "owner email mismatch"


def test_public_auth_fails_closed_on_wrong_audience(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest(
            "/mcp",
            {CF_ACCESS_JWT_HEADER: token(private_key, "owner@example.com", aud="other-aud")},
        )
    )

    assert decision.ok is False
    assert decision.status_code == 401


def test_public_auth_fails_closed_on_wrong_issuer(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest(
            "/mcp",
            {
                CF_ACCESS_JWT_HEADER: token(
                    private_key,
                    "owner@example.com",
                    issuer="https://other.cloudflareaccess.com",
                )
            },
        )
    )

    assert decision.ok is False
    assert decision.status_code == 401


def test_public_auth_fails_closed_on_expired_token(private_key) -> None:
    verifier = public_verifier(private_key)

    decision = verifier.verify_request(
        FakeRequest(
            "/mcp",
            {
                CF_ACCESS_JWT_HEADER: token(
                    private_key,
                    "owner@example.com",
                    expires_delta=timedelta(minutes=-5),
                )
            },
        )
    )

    assert decision.ok is False
    assert decision.status_code == 401


def test_public_auth_fails_closed_on_unverifiable_signature(private_key) -> None:
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = AuthVerifier(
        AuthConfig(
            mode="public",
            owner_email="owner@example.com",
            cloudflare_team_domain="https://team.cloudflareaccess.com",
            cloudflare_aud="aud",
        ),
        StaticJwkClient(wrong_key),
    )

    decision = verifier.verify_request(
        FakeRequest("/mcp", {CF_ACCESS_JWT_HEADER: token(private_key, "owner@example.com")})
    )

    assert decision.ok is False
    assert decision.status_code == 401


def test_public_auth_rejects_local_secret_header(private_key) -> None:
    verifier = AuthVerifier(
        AuthConfig(
            mode="public",
            owner_email="owner@example.com",
            cloudflare_team_domain="https://team.cloudflareaccess.com",
            cloudflare_aud="aud",
            local_secret="secret",
        ),
        StaticJwkClient(private_key),
    )

    decision = verifier.verify_request(FakeRequest("/mcp", {LOCAL_AUTH_HEADER: "secret"}))

    assert decision.ok is False
    assert decision.status_code == 401
    assert decision.reason == "missing Cloudflare Access token"


def test_local_auth_uses_separate_secret() -> None:
    verifier = AuthVerifier(AuthConfig(mode="local", local_secret="secret"))

    ok = verifier.verify_request(FakeRequest("/mcp", {LOCAL_AUTH_HEADER: "secret"}))
    bad = verifier.verify_request(FakeRequest("/mcp", {}))

    assert (ok.ok, ok.reason) == (True, "local")
    assert bad.ok is False
