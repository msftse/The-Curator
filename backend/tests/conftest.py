"""Shared fixtures.

Unit tests use no external services. Integration tests are marked with
`@pytest.mark.integration` and skipped automatically when emulators aren't
reachable on the conventional ports.
"""

from __future__ import annotations

import socket

import pytest


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
