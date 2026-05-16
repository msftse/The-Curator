"""Stub auth for M0: identifies the caller via the `X-User-Email` header.

M1 swaps this for Entra ID OIDC behind the same `User` model. Anything that
calls `get_current_user` does not need to change.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Header
from pydantic import BaseModel

from backend.core.config import Settings, get_settings
from backend.core.errors import Unauthorized

Role = Literal["contributor", "manager", "admin"]


class User(BaseModel):
    email: str
    roles: list[Role]

    def has_role(self, role: Role) -> bool:
        return role in self.roles


def _roles_for(email: str, settings: Settings) -> list[Role]:
    e = email.strip().lower()
    roles: list[Role] = ["contributor"]
    if e in settings.manager_email_set():
        roles.append("manager")
    if e in settings.admin_email_set():
        roles.append("admin")
    return roles


async def get_current_user(
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
) -> User:
    """FastAPI dep — returns the caller. Stub mode requires `X-User-Email`."""
    settings = get_settings()
    if settings.auth_mode != "stub":
        raise Unauthorized("only stub auth is implemented in M0")
    if not x_user_email:
        raise Unauthorized("missing X-User-Email header")
    return User(email=x_user_email.strip().lower(), roles=_roles_for(x_user_email, settings))


def require_role(role: Role):
    """Build a dep that enforces a single role."""

    async def _dep(user: User = ...) -> User:  # pragma: no cover - replaced via Depends
        return user

    # Real implementation uses Depends(get_current_user) — wired in api modules.
    from fastapi import Depends

    from backend.core.errors import Forbidden

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if not user.has_role(role):
            raise Forbidden(f"role {role!r} required")
        return user

    return _checker
