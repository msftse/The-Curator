"""Unit tests for backend.services.llm.foundry.FoundryLLMProvider.

These tests stub MAF's `FoundryChatClient.get_response` so we exercise the
provider's own parsing/observability code without hitting Azure. The key
regression they pin down is the `UsageDetails` access pattern:

`UsageDetails` is a TypedDict (plain `dict` at runtime), not a dataclass.
`getattr(usage, "input_token_count", 0)` silently returns 0; reads must
use dict access. See .agents/GAPS.md gap #2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.core.config import Settings
from backend.services.llm.foundry import FoundryLLMProvider


@dataclass
class _FakeResponse:
    text: str
    usage_details: dict[str, Any] | None  # mirrors MAF's TypedDict
    model: str | None


class _FakeChatClient:
    """Stands in for agent_framework.foundry.FoundryChatClient."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def get_response(
        self, messages: Any, *, options: dict[str, Any] | None = None
    ) -> _FakeResponse:
        self.calls.append({"messages": messages, "options": options})
        return self._response


def _make_provider(response: _FakeResponse) -> tuple[FoundryLLMProvider, _FakeChatClient]:
    """Build a provider with its lazy MAF client pre-populated."""
    settings = Settings(
        azure_ai_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        foundry_deployment="gpt-4o",
        # Skip Entra path entirely — the fake client never touches credentials.
        azure_ai_foundry_api_key="fake-key-not-used",
    )
    provider = FoundryLLMProvider(settings)
    client = _FakeChatClient(response)
    provider._client = client  # type: ignore[assignment]  # private bypass for tests
    return provider, client


@pytest.mark.asyncio
async def test_complete_reads_usage_details_via_dict_access() -> None:
    """Regression: UsageDetails is a TypedDict; must read via .get, not getattr."""
    response = _FakeResponse(
        text='{"category": "devops", "tags": ["x"]}',
        usage_details={"input_token_count": 137, "output_token_count": 29},
        model="gpt-4o",
    )
    provider, _client = _make_provider(response)

    result = await provider.complete(
        system="sys",
        user="usr",
        max_input_tokens=1000,
        max_output_tokens=200,
    )

    assert result.input_tokens == 137, "TypedDict access regression — getattr returns 0 silently"
    assert result.output_tokens == 29
    assert result.model_id == "gpt-4o"
    assert result.text.startswith("{")


@pytest.mark.asyncio
async def test_complete_handles_missing_usage_details() -> None:
    """When MAF omits usage entirely we still produce valid ints."""
    response = _FakeResponse(text="ok", usage_details=None, model="gpt-4o")
    provider, _client = _make_provider(response)

    result = await provider.complete(
        system="s",
        user="u",
        max_input_tokens=100,
        max_output_tokens=50,
    )

    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.model_id == "gpt-4o"


@pytest.mark.asyncio
async def test_complete_falls_back_to_deployment_name_when_model_missing() -> None:
    """No `model` echo from MAF → settings.foundry_deployment is the answer."""
    response = _FakeResponse(text="ok", usage_details={}, model=None)
    provider, _client = _make_provider(response)

    result = await provider.complete(
        system="s",
        user="u",
        max_input_tokens=100,
        max_output_tokens=50,
    )

    assert result.model_id == "gpt-4o"  # from Settings.foundry_deployment


@pytest.mark.asyncio
async def test_complete_passes_response_format_class_through() -> None:
    """Pydantic schema is forwarded to MAF as `options['response_format']`."""
    from pydantic import BaseModel

    class _Schema(BaseModel):
        verdict: str

    response = _FakeResponse(
        text='{"verdict": "keep"}',
        usage_details={"input_token_count": 5, "output_token_count": 3},
        model="gpt-4o",
    )
    provider, client = _make_provider(response)

    await provider.complete(
        system="s",
        user="u",
        max_input_tokens=100,
        max_output_tokens=50,
        response_format=_Schema,
    )

    assert len(client.calls) == 1
    assert client.calls[0]["options"]["response_format"] is _Schema


@pytest.mark.asyncio
async def test_complete_omits_response_format_for_literal_modes() -> None:
    """`"text"` / `"json_object"` are caller hints, not MAF arguments."""
    response = _FakeResponse(
        text="hi", usage_details={"input_token_count": 1, "output_token_count": 1}, model="gpt-4o"
    )
    provider, client = _make_provider(response)

    await provider.complete(
        system="s",
        user="u",
        max_input_tokens=100,
        max_output_tokens=50,
        response_format="json_object",
    )

    assert "response_format" not in client.calls[0]["options"]
