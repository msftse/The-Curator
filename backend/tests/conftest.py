"""Shared fixtures.

Unit tests use no external services. Integration tests are marked with
`@pytest.mark.integration` and skipped automatically when emulators aren't
reachable on the conventional ports.

To run integration tests against real Azure resources (read from
`.env.local`), set `SKILLHUB_RUN_INTEGRATION=1` in the environment — the
local-port gate is bypassed.

M1 adds `as_user(email)` and `as_admin(email)` fixtures that override the
running app's `IdentityProvider` with an in-memory provider returning a
configured `User`. This replaces the M0 `X-User-Email` test header pattern
but keeps it working for stub-mode tests.

`isolated_settings` (autouse) makes `Settings()` ignore the developer's
`.env.local`. Without it, any Settings instantiation in a unit test would
inherit whatever live config the operator currently has — including real
tenant IDs and AUTH_MODE=oidc — which makes assertions like
`auth_mode == 'stub'` non-deterministic depending on the host.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Iterator

import pytest

from backend.core.auth.models import Role, User
from backend.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make `Settings()` deterministic regardless of host env / `.env.local`.

    Pydantic-settings reads `.env.local` from `model_config.env_file` and
    process env vars on every `Settings()` call. For unit tests we want
    defaults-only behaviour — the test should set whatever fields it cares
    about explicitly. We blank the env_file for the duration of the test
    and unset every env var Settings reads.
    """
    original_env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    # Drop any host env vars whose names map to Settings fields. pydantic-
    # settings is case-insensitive, so this covers both `AUTH_MODE` and
    # `auth_mode`.
    field_names = set(Settings.model_fields)
    for var in list(os.environ):
        if var.lower() in field_names:
            monkeypatch.delenv(var, raising=False)
    try:
        yield
    finally:
        Settings.model_config["env_file"] = original_env_file


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless the stack is reachable.

    Two ways to qualify:
    - Local emulators on 8081 / 10000 / 6379 (docker compose up -d).
    - `SKILLHUB_RUN_INTEGRATION=1` set — caller asserts that `.env.local`
      points at a stack they want to test against (real Azure dev, etc.).
    """
    if os.environ.get("SKILLHUB_RUN_INTEGRATION") == "1":
        return
    cosmos_up = _port_open("localhost", 8081)
    azurite_up = _port_open("localhost", 10000)
    redis_up = _port_open("localhost", 6379)
    stack_up = cosmos_up and azurite_up and redis_up
    if stack_up:
        return
    skip = pytest.mark.skip(reason="emulator stack not running (docker compose up -d)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


class _InMemoryProvider:
    """Test-only IdentityProvider that always resolves to a fixed User."""

    def __init__(self, user: User) -> None:
        self._user = user

    async def resolve(self, request) -> User:  # noqa: ARG002
        return self._user


@pytest.fixture
def as_user() -> Callable[..., None]:
    """Yield a function that swaps `app.state.identity_provider` for a user-returning stub.

    Usage:

        def test_thing(client, as_user):
            as_user(client.app, email="alice@org")
            r = client.get("/v1/skills")
            ...
    """

    def _apply(app, *, email: str = "alice@org", roles: list[Role] | None = None) -> None:
        u = User(email=email, roles=roles or ["user"])
        app.state.identity_provider = _InMemoryProvider(u)

    return _apply


@pytest.fixture
def as_admin() -> Callable[..., None]:
    def _apply(app, *, email: str = "admin@org") -> None:
        u = User(email=email, roles=["user", "admin"])
        app.state.identity_provider = _InMemoryProvider(u)

    return _apply
