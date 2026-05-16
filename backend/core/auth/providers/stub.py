"""Stub identity provider — M0 behavior, lifted verbatim.

Looks for `X-User-Email` on the request. Used for local docker-compose dev
and unit tests; never enabled in cloud.
"""

from __future__ import annotations

from fastapi import Request

from backend.core.auth.models import User, roles_for_email
from backend.core.config import Settings
from backend.core.errors import Unauthorized


class StubIdentityProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def resolve(self, request: Request) -> User:
        email = request.headers.get("X-User-Email")
        if not email:
            raise Unauthorized("missing X-User-Email header")
        email = email.strip().lower()
        return User(email=email, roles=roles_for_email(email, self._settings))
