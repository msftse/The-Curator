"""Unit tests for the Settings validator added in M1."""

from __future__ import annotations

import pytest

from backend.core.config import Settings


def test_default_settings_boot_with_no_env():
    s = Settings()
    assert s.auth_mode == "stub"
    assert s.resolved_oidc_issuer() == ""


def test_oidc_mode_requires_entra_settings():
    with pytest.raises(ValueError):
        Settings(auth_mode="oidc")


def test_oidc_mode_happy_path():
    s = Settings(
        auth_mode="oidc",
        entra_tenant_id="tenant-x",
        entra_client_id="client-x",
        entra_group_id_admin="group-x",
    )
    assert s.resolved_oidc_issuer() == "https://login.microsoftonline.com/tenant-x/v2.0"
    assert "discovery/v2.0/keys" in s.resolved_oidc_jwks_url()


def test_fake_oidc_mode_does_not_require_entra_settings():
    s = Settings(auth_mode="fake_oidc")
    assert s.auth_mode == "fake_oidc"


def test_enforce_production_safety_refuses_stub_outside_local_dev():
    s = Settings(auth_mode="stub", local_dev=False)
    with pytest.raises(RuntimeError, match="LOCAL_DEV=1"):
        s.enforce_production_safety()


def test_enforce_production_safety_refuses_fake_oidc_outside_local_dev():
    s = Settings(auth_mode="fake_oidc", local_dev=False)
    with pytest.raises(RuntimeError, match="LOCAL_DEV=1"):
        s.enforce_production_safety()


def test_enforce_production_safety_allows_stub_with_local_dev():
    s = Settings(auth_mode="stub", local_dev=True)
    s.enforce_production_safety()  # must not raise


def test_enforce_production_safety_allows_oidc():
    s = Settings(
        auth_mode="oidc",
        local_dev=False,
        entra_tenant_id="t",
        entra_client_id="c",
        entra_group_id_admin="g",
    )
    s.enforce_production_safety()  # must not raise
