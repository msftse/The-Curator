"""Real OIDC provider — validates Entra-issued JWTs.

Pulls the tenant JWKS once (TTL-cached in-process for an hour) and verifies
each incoming bearer token's signature + `iss` + `aud` + `exp` + `nbf`.
Admin role is derived from the `ENTRA_GROUP_ID_ADMIN` membership in the
`groups` claim (with an `app_role` fallback for the app-roles alternative).

Gotcha (documented in the plan): Entra omits the `groups` claim entirely when
the user is a member of >150 groups. The provider logs a WARN in that case
and grants `user` only; the documented mitigation is to switch the App
Registration to **app roles**, which emits a bounded `roles` claim instead.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from fastapi import Request
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from backend.core.auth.models import Role, User
from backend.core.config import Settings
from backend.core.errors import InvalidToken, Unauthorized
from backend.core.logging import get_logger

log = get_logger(__name__)


class _JwksCache:
    """Process-local JWKS cache. TTL refresh on `kid` miss."""

    def __init__(self, url: str, ttl_seconds: int) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._client: PyJWKClient | None = None
        self._fetched_at: float = 0.0

    def _stale(self) -> bool:
        return self._client is None or (time.time() - self._fetched_at) > self._ttl

    def get_signing_key(self, token: str):
        if self._stale():
            self._client = PyJWKClient(self._url)
            self._fetched_at = time.time()
        assert self._client is not None
        try:
            return self._client.get_signing_key_from_jwt(token)
        except Exception:
            # `kid` miss — refresh and retry once.
            self._client = PyJWKClient(self._url)
            self._fetched_at = time.time()
            return self._client.get_signing_key_from_jwt(token)


class OidcIdentityProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._issuer = settings.resolved_oidc_issuer()
        self._jwks_url = settings.resolved_oidc_jwks_url()
        self._audience = settings.entra_client_id
        self._admin_group = settings.entra_group_id_admin
        if not self._jwks_url:
            raise ValueError("OIDC requires entra_tenant_id (or oidc_jwks_url)")
        self._jwks = _JwksCache(self._jwks_url, settings.oidc_jwks_cache_ttl_seconds)
        # Allow tests to inject a custom verifier (`verify_token`) without
        # going through PyJWKClient.
        self._verifier = None

    def set_verifier(self, fn) -> None:
        """Test seam: replace JWKS-based verification with an inline function."""
        self._verifier = fn

    def _verify(self, token: str) -> dict[str, Any]:
        if self._verifier is not None:
            return self._verifier(token)
        try:
            signing_key = self._jwks.get_signing_key(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except PyJWTError as exc:
            raise InvalidToken(f"token verification failed: {exc}") from exc
        except Exception as exc:  # JWKS fetch errors etc.
            raise InvalidToken(f"token verification failed: {exc}") from exc

    def _claims_to_user(self, claims: dict[str, Any]) -> User:
        email = claims.get("preferred_username") or claims.get("email") or claims.get("upn") or ""
        email = email.strip().lower()
        if not email:
            raise InvalidToken("token has no email/preferred_username claim")

        groups = claims.get("groups") or []
        app_roles = claims.get("roles") or []
        roles: list[Role] = ["user"]
        if not groups and not app_roles:
            log.warning(
                "oidc_no_groups_or_roles_claim",
                extra={"email": email, "hint": "switch app registration to app roles?"},
            )
        if self._admin_group and self._admin_group in groups or "admin" in app_roles:
            roles.append("admin")
        return User(email=email, roles=roles)

    async def resolve(self, request: Request) -> User:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            raise Unauthorized("missing bearer token")
        token = auth.split(None, 1)[1].strip()
        claims = self._verify(token)
        return self._claims_to_user(claims)


# Convenience export for test mocking — never used in prod code.
__all__ = ["OidcIdentityProvider", "httpx"]
