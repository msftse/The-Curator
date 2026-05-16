"""M3 — FakeLLMProvider tests."""

from __future__ import annotations

import pytest

from backend.services.llm import FakeLLMProvider, LLMResult


@pytest.mark.asyncio
async def test_fake_provider_returns_canned_in_order():
    r1 = LLMResult(text="one", input_tokens=1, output_tokens=2, model_id="m")
    r2 = LLMResult(text="two", input_tokens=3, output_tokens=4, model_id="m")
    p = FakeLLMProvider(canned=[r1, r2])
    out1 = await p.complete(system="s", user="u")
    out2 = await p.complete(system="s", user="u2")
    assert out1.text == "one"
    assert out2.text == "two"


@pytest.mark.asyncio
async def test_fake_provider_records_calls():
    r = LLMResult(text="x", input_tokens=0, output_tokens=0, model_id="m")
    p = FakeLLMProvider(canned=[r])
    await p.complete(system="sys-a", user="usr-a", max_output_tokens=42)
    assert len(p.calls) == 1
    assert p.calls[0]["system"] == "sys-a"
    assert p.calls[0]["user"] == "usr-a"
    assert p.calls[0]["max_output_tokens"] == 42


@pytest.mark.asyncio
async def test_fake_provider_raises_when_empty():
    p = FakeLLMProvider(canned=[])
    with pytest.raises(Exception):
        await p.complete(system="s", user="u")
