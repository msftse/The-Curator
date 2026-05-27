"""Defender service — LLM-only skill security scanner (M5-2).

Public API:

* ``DefenderScanner`` — Protocol the worker depends on.
* ``FoundryDefenderScanner`` — production scanner; uses the shared
  ``FoundryLLMProvider`` to call Azure AI Foundry with structured output =
  ``DefenderReport``.
* ``FakeDefenderScanner`` — deterministic test double. Returns canned
  reports or derives a clean report from input shape.
* ``make_scanner(provider, *, settings)`` — factory used by the worker.

Storage policy: this module performs NO Cosmos / Redis / Blob I/O. It is
exempt from the never-delete AST gate.
"""

from __future__ import annotations

from backend.services.defender.scanner import (
    DefenderScanner,
    FakeDefenderScanner,
    FoundryDefenderScanner,
    make_scanner,
)

__all__ = [
    "DefenderScanner",
    "FakeDefenderScanner",
    "FoundryDefenderScanner",
    "make_scanner",
]
