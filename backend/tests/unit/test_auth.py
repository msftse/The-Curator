from __future__ import annotations

from backend.core.auth import User, _roles_for, principal_actor, roles_for_email
from backend.core.auth.models import ServiceAccount
from backend.core.auth.providers.base import select_provider
from backend.core.config import Settings


def test_role_assignment_two_role_split():
    s = Settings(admin_emails="admin@org", manager_emails="")
    assert roles_for_email("contributor@org", s) == ["user"]
    assert "admin" in roles_for_email("admin@org", s)
    assert "user" in roles_for_email("admin@org", s)


def test_legacy_manager_email_still_grants_admin_for_one_release():
    s = Settings(manager_emails="manager@org", admin_emails="admin@org")
    # The legacy alias keeps working — both buckets map to `admin`.
    assert "admin" in roles_for_email("manager@org", s)
    assert "admin" in roles_for_email("admin@org", s)
    # A regular user is just `user`.
    assert roles_for_email("contributor@org", s) == ["user"]


def test_role_emails_are_case_insensitive():
    s = Settings(admin_emails="Admin@Org")
    assert "admin" in roles_for_email("admin@org", s)


def test_user_has_role():
    u = User(email="m@org", roles=["user", "admin"])
    assert u.has_role("admin")
    assert u.has_role("user")


def test_back_compat_underscore_roles_for_alias():
    s = Settings(admin_emails="admin@org")
    assert _roles_for("admin@org", s) == roles_for_email("admin@org", s)


def test_principal_actor_human_vs_machine():
    u = User(email="alice@org", roles=["user"])
    sa = ServiceAccount(service_account_id="abc123", name="bot", scopes=["catalog:read"])
    assert principal_actor(u) == "alice@org"
    assert principal_actor(sa) == "svc:abc123"


def test_select_provider_stub_default():
    s = Settings()
    provider = select_provider(s)
    assert provider.__class__.__name__ == "StubIdentityProvider"


def test_select_provider_fake_oidc():
    s = Settings(auth_mode="fake_oidc")
    provider = select_provider(s)
    assert provider.__class__.__name__ == "FakeOidcIdentityProvider"


def test_select_provider_saml_returns_concrete_class():
    s = Settings(auth_mode="saml")
    provider = select_provider(s)
    assert provider.__class__.__name__ == "SamlIdentityProvider"


async def test_saml_provider_raises_not_implemented():
    import pytest

    s = Settings(auth_mode="saml")
    provider = select_provider(s)
    # We pass None for the request — the provider should fail before touching it.
    with pytest.raises(NotImplementedError):
        await provider.resolve(None)  # type: ignore[arg-type]
