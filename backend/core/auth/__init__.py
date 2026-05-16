"""Public surface of `backend.core.auth`.

Existing imports keep working:

    from backend.core.auth import User, get_current_user, require_role

M1 additions:

    from backend.core.auth import (
        Principal, ServiceAccount, get_principal, require_scope,
    )
"""

from __future__ import annotations

from backend.core.auth.api_keys import (
    generate_raw_key,
    issue,
    resolve_api_key,
    revoke,
)
from backend.core.auth.deps import (
    get_current_user,
    get_identity_provider,
    get_principal,
    require_role,
    require_scope,
)
from backend.core.auth.models import (
    Principal,
    Role,
    Scope,
    ServiceAccount,
    User,
    principal_actor,
    roles_for_email,
)
from backend.core.auth.providers.base import IdentityProvider, select_provider

# Back-compat alias for the single existing test that imports it.
_roles_for = roles_for_email

__all__ = [
    "IdentityProvider",
    "Principal",
    "Role",
    "Scope",
    "ServiceAccount",
    "User",
    "_roles_for",
    "generate_raw_key",
    "get_current_user",
    "get_identity_provider",
    "get_principal",
    "issue",
    "principal_actor",
    "require_role",
    "require_scope",
    "resolve_api_key",
    "revoke",
    "roles_for_email",
    "select_provider",
]
