"""LLMProvider ABC + LLMResult.

M3 has exactly two implementations: ``FoundryLLMProvider`` (prod + dev) and
``FakeLLMProvider`` (tests). Adding any other provider (OpenAI / Anthropic /
etc.) is explicitly out-of-scope for M3 — see ``AGENTS.md`` and the M3 plan.

This module performs no Cosmos / Blob / Redis I/O — and therefore has no
``delete_item`` / ``delete_blob`` calls. The AST gate at
``backend/tests/unit/test_never_delete_invariant.py`` still scans this file
as a guarded module so future edits inherit the same constraint.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

# Re-exported so callers can ``from backend.services.llm import LLMProviderError``.
from backend.core.errors import LLMProviderError  # noqa: F401


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model_id: str


# `response_format` accepts the legacy literals ("text"/"json_object") for the
# curator and an opt-in Pydantic model class for structured outputs. When a
# model class is passed, MAF validates the response against it server-side
# (Azure AI Foundry structured outputs); the returned `LLMResult.text` is
# still the raw JSON string, so callers can parse with `Model.model_validate_json`.
ResponseFormat = Literal["text", "json_object"] | type[Any]


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_input_tokens: int,
        max_output_tokens: int,
        response_format: ResponseFormat = "json_object",
        temperature: float = 0.0,
    ) -> LLMResult: ...
