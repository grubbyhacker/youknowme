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


def token(private_key, email: str, aud: str = "aud") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "https://team.cloudflareaccess.com",
            "aud": aud,
            "email": email,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )


def test_public_auth_accepts_valid_owner_jwt(private_key) -> None:
    verifier = AuthVerifier(
        AuthConfig(
            mode="public",
            owner_email="owner@example.com",
            cloudflare_team_domain="https://team.cloudflareaccess.com",
            cloudflare_aud="aud",
        ),
        StaticJwkClient(private_key),
    )

    ok, reason = verifier.verify_request(
        FakeRequest("/mcp", {CF_ACCESS_JWT_HEADER: token(private_key, "owner@example.com")})
    )

    assert ok is True
    assert reason == "cloudflare"


def test_public_auth_fails_closed_on_wrong_email(private_key) -> None:
    verifier = AuthVerifier(
        AuthConfig(
            mode="public",
            owner_email="owner@example.com",
            cloudflare_team_domain="https://team.cloudflareaccess.com",
            cloudflare_aud="aud",
        ),
        StaticJwkClient(private_key),
    )

    ok, _reason = verifier.verify_request(
        FakeRequest("/mcp", {CF_ACCESS_JWT_HEADER: token(private_key, "other@example.com")})
    )

    assert ok is False


def test_local_auth_uses_separate_secret() -> None:
    verifier = AuthVerifier(AuthConfig(mode="local", local_secret="secret"))

    ok, reason = verifier.verify_request(FakeRequest("/mcp", {LOCAL_AUTH_HEADER: "secret"}))
    bad, _ = verifier.verify_request(FakeRequest("/mcp", {}))

    assert (ok, reason) == (True, "local")
    assert bad is False

