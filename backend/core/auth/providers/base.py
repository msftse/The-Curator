"""`IdentityProvider` Protocol + `select_provider()` factory.

The seam: every route handler depends on `get_current_user` which delegates
to `app.state.identity_provider`. No route imports a concrete provider class.
Adding SAML in a future milestone is a new file in this directory + an
`AUTH_MODE=saml` env value — no caller changes.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from backend.core.auth.models import User
from backend.core.config import Settings


class IdentityProvider(Protocol):
    """Resolves an incoming request into a `User`.

    Implementations:
    - `StubIdentityProvider`  — reads `X-User-Email`, M0 behavior.
    - `FakeOidcIdentityProvider` — local dev OIDC stand-in (no real Entra tenant).
    - `OidcIdentityProvider`  — validates Entra-issued JWTs.
    - `SamlIdentityProvider`  — placeholder, raises NotImplementedError.
    """

    async def resolve(self, request: Request) -> User: ...


def select_provider(settings: Settings) -> IdentityProvider:
    """Pick a provider based on `settings.auth_mode`."""
    # Lazy imports to keep the import graph shallow and to avoid forcing
    # PyJWT to import in environments that never need it (e.g. stub-only CI).
    if settings.auth_mode == "stub":
        from backend.core.auth.providers.stub import StubIdentityProvider

        return StubIdentityProvider(settings)
    if settings.auth_mode == "fake_oidc":
        from backend.core.auth.providers.fake import FakeOidcIdentityProvider

        return FakeOidcIdentityProvider(settings)
    if settings.auth_mode == "oidc":
        from backend.core.auth.providers.oidc import OidcIdentityProvider

        return OidcIdentityProvider(settings)
    if settings.auth_mode == "saml":
        from backend.core.auth.providers.saml import SamlIdentityProvider

        return SamlIdentityProvider(settings)
    raise ValueError(f"unknown auth_mode: {settings.auth_mode!r}")
