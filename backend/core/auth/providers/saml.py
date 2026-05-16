"""Placeholder SAML provider.

Exists so `select_provider("saml")` returns a concrete class — the
abstraction stays honest. When a SAML-only customer lands, replace
`resolve()` with a real implementation; no caller changes elsewhere.
"""

from __future__ import annotations

from fastapi import Request

from backend.core.auth.models import User
from backend.core.config import Settings


class SamlIdentityProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def resolve(self, request: Request) -> User:
        raise NotImplementedError(
            "SAML provider lands in a future milestone — "
            "see .agents/plans/m1-azure-deployment-and-auth.md NOTES"
        )
