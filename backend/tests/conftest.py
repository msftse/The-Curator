"""Shared fixtures.

Unit tests use no external services. Integration tests are marked with
`@pytest.mark.integration` and skipped automatically when emulators aren't
reachable on the conventional ports.

M1 adds `as_user(email)` and `as_admin(email)` fixtures that override the
running app's `IdentityProvider` with an in-memory provider returning a
configured `User`. This replaces the M0 `X-User-Email` test header pattern
but keeps it working for stub-mode tests.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import pytest

from backend.core.auth.models import Role, User


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    """Skip integration tests if the emulator stack isn't up."""
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
