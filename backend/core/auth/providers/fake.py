"""Fake OIDC provider for local dev — mimics the real OIDC contract.

Use case: `AUTH_MODE=fake_oidc` lets developers exercise the real
`Authorization: Bearer <jwt>` code path end-to-end without standing up an
Entra tenant. Tokens are signed by a process-local RSA keypair generated
at provider boot.

How to mint a dev token (from inside a Python shell or a script):

    from backend.core.auth.providers.fake import FakeOidcIdentityProvider
    from backend.core.config import get_settings
    p = FakeOidcIdentityProvider(get_settings())
    print(p.mint_token("alice@org", roles=["admin"]))

This file deliberately mirrors `OidcIdentityProvider`'s claim shape so the
two are wire-compatible — the cloud provider's `resolve()` would accept a
fake-minted token if you handed it the same public key.

THIS IS NOT A PROD-SAFE CODE PATH. The `_validate_oidc` model validator in
`config.py` only gates real `oidc`; `fake_oidc` is allowed without an Entra
tenant on purpose.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from jwt.exceptions import PyJWTError

from backend.core.auth.models import Role, User
from backend.core.config import Settings
from backend.core.errors import InvalidToken, Unauthorized

# Fake-only constants — these are NOT secrets.
FAKE_ISSUER = "https://fake-oidc.local/v2.0"
FAKE_AUDIENCE = "fake-skillhub-client"
FAKE_ADMIN_GROUP = "fake-admin-group-id"


def _generate_keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeOidcIdentityProvider:
    """OIDC-shaped provider with no real tenant.

    On boot, generates a fresh RSA keypair and uses it both to sign minted
    tokens (`mint_token`) and to verify incoming ones (`resolve`). This is
    enough to exercise every code path the real `OidcIdentityProvider`
    walks — bearer parsing, signature check, claims mapping — without
    network or external config.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._private_key = _generate_keypair()
        self._public_key = self._private_key.public_key()
        self._issuer = settings.oidc_issuer or FAKE_ISSUER
        self._audience = settings.entra_client_id or FAKE_AUDIENCE
        self._admin_group = settings.entra_group_id_admin or FAKE_ADMIN_GROUP
        # Match Entra: 1h tokens.
        self._ttl = 3600

    @property
    def public_pem(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def mint_token(
        self,
        email: str,
        *,
        roles: list[Role] | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """Mint a fake-OIDC JWT shaped like a real Entra v2.0 token."""
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl,
            "sub": uuid.uuid4().hex,
            "preferred_username": email,
            "email": email,
            "groups": [],
        }
        if roles and "admin" in roles:
            claims["groups"] = [self._admin_group]
        if extra_claims:
            claims.update(extra_claims)
        priv_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return jwt.encode(claims, priv_pem, algorithm="RS256")

    def _verify(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(
                token,
                self.public_pem,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except PyJWTError as exc:
            raise InvalidToken(f"fake_oidc token verification failed: {exc}") from exc

    async def resolve(self, request: Request) -> User:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            raise Unauthorized("missing bearer token")
        token = auth.split(None, 1)[1].strip()
        claims = self._verify(token)
        email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
        if not email:
            raise InvalidToken("token has no email claim")
        groups = claims.get("groups") or []
        roles: list[Role] = ["user"]
        if self._admin_group in groups:
            roles.append("admin")
        return User(email=email, roles=roles)
