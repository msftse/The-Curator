"""Unit tests for the fake-OIDC provider (local-dev OIDC stand-in)."""

from __future__ import annotations

import pytest

from backend.core.auth.providers.fake import FakeOidcIdentityProvider
from backend.core.config import Settings
from backend.core.errors import InvalidToken, Unauthorized


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _provider() -> FakeOidcIdentityProvider:
    return FakeOidcIdentityProvider(Settings(auth_mode="fake_oidc"))


async def test_fake_oidc_mint_and_resolve_user():
    p = _provider()
    token = p.mint_token("alice@org")
    user = await p.resolve(_FakeRequest({"Authorization": f"Bearer {token}"}))  # type: ignore[arg-type]
    assert user.email == "alice@org"
    assert user.roles == ["user"]


async def test_fake_oidc_mint_admin_token():
    p = _provider()
    token = p.mint_token("boss@org", roles=["admin"])
    user = await p.resolve(_FakeRequest({"Authorization": f"Bearer {token}"}))  # type: ignore[arg-type]
    assert "admin" in user.roles


async def test_fake_oidc_missing_header():
    p = _provider()
    with pytest.raises(Unauthorized):
        await p.resolve(_FakeRequest({}))  # type: ignore[arg-type]


async def test_fake_oidc_tampered_token():
    p = _provider()
    token = p.mint_token("alice@org")
    tampered = token + "x"
    with pytest.raises(InvalidToken):
        await p.resolve(_FakeRequest({"Authorization": f"Bearer {tampered}"}))  # type: ignore[arg-type]
