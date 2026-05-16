from __future__ import annotations

from backend.core.auth import User, _roles_for
from backend.core.config import Settings


def test_role_assignment():
    s = Settings(manager_emails="manager@org", admin_emails="admin@org")
    assert "manager" in _roles_for("manager@org", s)
    assert "admin" not in _roles_for("manager@org", s)
    assert _roles_for("contributor@org", s) == ["contributor"]
    assert set(_roles_for("admin@org", s)) >= {"contributor", "admin"}


def test_user_has_role():
    u = User(email="m@org", roles=["contributor", "manager"])
    assert u.has_role("manager")
    assert not u.has_role("admin")


def test_role_emails_are_case_insensitive():
    s = Settings(manager_emails="Manager@Org")
    assert "manager" in _roles_for("manager@org", s)
