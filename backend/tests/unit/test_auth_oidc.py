"""Unit tests for the real OIDC provider.

We don't talk to Entra. Instead we substitute a process-local RSA keypair
and call `set_verifier` to replace JWKS-based verification with an inline
`jwt.decode`. The provider's claim-mapping logic is what we want to assert.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.core.auth.providers.oidc import OidcIdentityProvider
from backend.core.config import Settings
from backend.core.errors import InvalidToken, Unauthorized


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return priv_pem, pub_pem


def _settings(**overrides) -> Settings:
    return Settings(
        auth_mode="oidc",
        entra_tenant_id="11111111-1111-1111-1111-111111111111",
        entra_client_id="api://skillhub-dev",
        entra_group_id_admin="admins-group-id",
        **overrides,
    )


def _provider_with_pub(priv_pem: bytes, pub_pem: bytes, settings: Settings):
    p = OidcIdentityProvider(settings)

    def _verify(token: str) -> dict:
        try:
            return jwt.decode(
                token,
                pub_pem,
                algorithms=["RS256"],
                audience=settings.entra_client_id,
                issuer=settings.resolved_oidc_issuer(),
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception as exc:
            # Mirror what the real `_verify()` does — wrap as InvalidToken so
            # the provider's behavior contract is exercised by the tests.
            raise InvalidToken(str(exc)) from exc

    p.set_verifier(_verify)
    return p


def _make_token(priv_pem: bytes, settings: Settings, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.resolved_oidc_issuer(),
        "aud": settings.entra_client_id,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "preferred_username": "alice@org",
        "groups": [],
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, priv_pem, algorithm="RS256")


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


async def test_oidc_happy_path_user_role_only():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    token = _make_token(priv, s)
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    user = await p.resolve(req)  # type: ignore[arg-type]
    assert user.email == "alice@org"
    assert user.roles == ["user"]


async def test_oidc_admin_group_grants_admin_role():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    token = _make_token(priv, s, groups=["admins-group-id"])
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    user = await p.resolve(req)  # type: ignore[arg-type]
    assert "admin" in user.roles


async def test_oidc_app_roles_fallback_grants_admin():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    token = _make_token(priv, s, roles=["admin"])
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    user = await p.resolve(req)  # type: ignore[arg-type]
    assert "admin" in user.roles


async def test_oidc_missing_bearer_raises_unauthorized():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    req = _FakeRequest({})
    with pytest.raises(Unauthorized):
        await p.resolve(req)  # type: ignore[arg-type]


async def test_oidc_wrong_audience_raises_invalid_token():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    token = _make_token(priv, s, aud="wrong-aud")
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    with pytest.raises(InvalidToken):
        await p.resolve(req)  # type: ignore[arg-type]


async def test_oidc_expired_token_raises_invalid_token():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    now = int(time.time())
    token = _make_token(priv, s, exp=now - 10, iat=now - 100, nbf=now - 100)
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    with pytest.raises(InvalidToken):
        await p.resolve(req)  # type: ignore[arg-type]


async def test_oidc_tampered_token_raises_invalid_token():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    token = _make_token(priv, s)
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    req = _FakeRequest({"Authorization": f"Bearer {tampered}"})
    with pytest.raises(InvalidToken):
        await p.resolve(req)  # type: ignore[arg-type]


async def test_oidc_no_email_claim_raises_invalid_token():
    priv, pub = _keypair()
    s = _settings()
    p = _provider_with_pub(priv, pub, s)
    # Build a token without preferred_username/email.
    now = int(time.time())
    claims = {
        "iss": s.resolved_oidc_issuer(),
        "aud": s.entra_client_id,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "groups": [],
    }
    token = jwt.encode(claims, priv, algorithm="RS256")
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    with pytest.raises(InvalidToken):
        await p.resolve(req)  # type: ignore[arg-type]
