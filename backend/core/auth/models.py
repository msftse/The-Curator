"""Auth domain models.

`Role` is collapsed to two values for M1: `user` and `admin`. The legacy
`contributor`/`manager` names are gone from the public type but the stub
provider still honors `manager_emails` for one release with a deprecation
warning so that local .env files don't break overnight.

`Principal = User | ServiceAccount` is the union every route handler that
accepts both humans and machine clients depends on via `get_principal`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.core.config import Settings

# Two-role collapse: `user` (anyone in the org) and `admin` (review queue,
# approve/reject, classification override, future curator controls).
Role = Literal["user", "admin"]

# API key scopes — bounded list (Pydantic `Literal` enforces).
Scope = Literal["catalog:read", "usage:write"]


class User(BaseModel):
    """A human principal — resolved from either the stub header or an OIDC JWT."""

    email: str
    roles: list[Role]

    def has_role(self, role: Role) -> bool:
        return role in self.roles


class ServiceAccount(BaseModel):
    """A machine principal — resolved from `Authorization: Bearer sh_live_...`."""

    service_account_id: str
    name: str
    scopes: list[Scope]

    def has_scope(self, scope: Scope) -> bool:
        return scope in self.scopes


Principal = User | ServiceAccount


def principal_actor(principal: Principal) -> str:
    """Audit `actor` convention: humans by email, machines as `svc:<id>`."""
    if isinstance(principal, User):
        return principal.email
    return f"svc:{principal.service_account_id}"


def roles_for_email(email: str, settings: Settings) -> list[Role]:
    """Map a stub/OIDC email to the two-role universe.

    Anyone in `settings.admin_email_set()` (or, deprecated, `manager_email_set()`)
    becomes `["user", "admin"]`. Everyone else is `["user"]`.
    """
    e = email.strip().lower()
    roles: list[Role] = ["user"]
    admins = settings.admin_email_set()
    legacy_managers = settings.manager_email_set()
    if e in admins or e in legacy_managers:
        roles.append("admin")
    return roles
