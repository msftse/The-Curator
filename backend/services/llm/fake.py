"""In-process fake LLM provider for tests.

Returns canned ``LLMResult`` outputs in FIFO order. Records every call's
kwargs on ``self.calls`` so tests can assert prompts.

Performs no I/O. The AST gate at
``backend/tests/unit/test_never_delete_invariant.py`` scans this module —
keep it free of ``delete_item`` / ``delete_blob`` calls.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

from backend.core.errors import LLMProviderError
from backend.services.llm.provider import LLMProvider, LLMResult


class FakeLLMProvider(LLMProvider):
    """Test-only LLMProvider. Constructed with a list of canned LLMResults."""

    def __init__(self, canned: Iterable[LLMResult] | None = None) -> None:
        self._q: deque[LLMResult] = deque(canned or [])
        self.calls: list[dict[str, Any]] = []

    def extend(self, more: Iterable[LLMResult]) -> None:
        """Append more canned responses (handy for multi-phase tests)."""
        self._q.extend(more)

    async def complete(
        self,
        **kwargs: Any,
    ) -> LLMResult:
        self.calls.append(dict(kwargs))
        if not self._q:
            raise LLMProviderError("FakeLLMProvider exhausted")
        return self._q.popleft()
