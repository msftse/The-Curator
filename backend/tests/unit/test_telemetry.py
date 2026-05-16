"""Unit tests for the telemetry init shim."""

from __future__ import annotations

from backend.core import telemetry
from backend.core.config import Settings


def test_configure_telemetry_noop_without_connection_string():
    telemetry.reset_for_tests()
    telemetry.configure_telemetry(Settings(appinsights_connection_string=""))
    # No exception, no crash. Module's idempotency flag is now set.
    assert telemetry._configured is True


def test_configure_telemetry_idempotent_calls():
    telemetry.reset_for_tests()
    s = Settings(appinsights_connection_string="")
    telemetry.configure_telemetry(s)
    telemetry.configure_telemetry(s)  # second call is a no-op
    assert telemetry._configured is True
