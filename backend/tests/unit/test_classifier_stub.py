from __future__ import annotations

import json

import pytest

from backend.core.errors import LLMProviderError
from backend.services.classifier_stub import (
    ALLOWED_CATEGORIES,
    LLMClassifier,
    StubClassifier,
    make_classifier,
)
from backend.services.llm.fake import FakeLLMProvider
from backend.services.llm.provider import LLMResult

SKILL_MD = """---
name: foo-skill
description: does foo
category: devops
tags: [k8s, ops, deploy]
---
This is the first line that becomes the summary.

More body text below.
"""


# ---- StubClassifier (now async) ---------------------------------------


async def test_stub_uses_frontmatter():
    c = await StubClassifier().classify(SKILL_MD)
    assert c.category == "devops"
    assert c.tags == ["k8s", "ops", "deploy"]
    assert c.quality_score == 70
    assert c.summary.startswith("This is the first line")
    assert c.classifier_version == "stub-v1"
    assert c.duplicate_candidates == []


async def test_stub_deterministic():
    a = await StubClassifier().classify(SKILL_MD)
    b = await StubClassifier().classify(SKILL_MD)
    assert a.category == b.category
    assert a.tags == b.tags
    assert a.summary == b.summary


async def test_stub_defaults_when_frontmatter_missing():
    c = await StubClassifier().classify("# no frontmatter\nbody")
    assert c.category == "uncategorized"
    assert c.tags == []


async def test_stub_tag_limit():
    md = (
        "---\nname: x\ndescription: y\ntags: ["
        + ",".join(f"t{i}" for i in range(20))
        + "]\n---\nbody\n"
    )
    c = await StubClassifier().classify(md)
    assert len(c.tags) == 8


def test_make_classifier_unknown():
    with pytest.raises(ValueError):
        make_classifier("not-a-real-provider")


def test_make_classifier_stub():
    assert isinstance(make_classifier("stub"), StubClassifier)


def test_make_classifier_llm_requires_settings():
    with pytest.raises(ValueError, match="settings="):
        make_classifier("llm")


# ---- LLMClassifier -----------------------------------------------------


def _llm_result(payload: dict) -> LLMResult:
    return LLMResult(
        text=json.dumps(payload),
        input_tokens=100,
        output_tokens=50,
        model_id="fake-gpt-4o",
    )


async def test_llm_classifier_happy_path():
    fake = FakeLLMProvider(
        [
            _llm_result(
                {
                    "category": "devops",
                    "tags": ["kubernetes", "deployment"],
                    "quality_score": 88,
                    "summary": "Deploy services to a Kubernetes cluster.",
                }
            )
        ]
    )
    classifier = LLMClassifier(fake)
    body = "# Kubernetes deployer\n\nDeploys services via kubectl.\n"
    c = await classifier.classify(body)
    assert c.category == "devops"
    assert c.tags == ["kubernetes", "deployment"]
    assert c.quality_score == 88
    assert c.summary == "Deploy services to a Kubernetes cluster."
    assert c.classifier_version == "llm-v1:fake-gpt-4o"
    # Verify the LLM saw the body, not the wrapping.
    assert "Kubernetes deployer" in fake.calls[0]["user"]


async def test_llm_classifier_frontmatter_wins_over_llm():
    fake = FakeLLMProvider(
        [
            _llm_result(
                {
                    "category": "creative",
                    "tags": ["react"],
                    "quality_score": 75,
                    "summary": "an llm summary",
                }
            )
        ]
    )
    classifier = LLMClassifier(fake)
    # SKILL_MD has frontmatter category=devops; LLM says creative; frontmatter wins.
    c = await classifier.classify(SKILL_MD)
    assert c.category == "devops"
    assert c.tags == ["k8s", "ops", "deploy"]


async def test_llm_classifier_clamps_category_to_allow_list():
    fake = FakeLLMProvider(
        [
            _llm_result(
                {
                    "category": "blockchain-nft-web3",  # not allowed
                    "tags": ["foo"],
                    "quality_score": 60,
                    "summary": "x",
                }
            )
        ]
    )
    classifier = LLMClassifier(fake)
    c = await classifier.classify("# body\nno frontmatter\n")
    assert c.category == "uncategorized"
    assert c.category in ALLOWED_CATEGORIES


async def test_llm_classifier_clamps_quality_score():
    fake = FakeLLMProvider(
        [
            _llm_result(
                {
                    "category": "devops",
                    "tags": [],
                    "quality_score": 999,
                    "summary": "x",
                }
            )
        ]
    )
    c = await LLMClassifier(fake).classify("# body\n")
    assert c.quality_score == 100


async def test_llm_classifier_falls_back_on_provider_error():
    class _Boom(FakeLLMProvider):
        async def complete(self, **kwargs):
            raise LLMProviderError("simulated outage")

    classifier = LLMClassifier(_Boom())
    c = await classifier.classify(SKILL_MD)
    # Falls back to stub — frontmatter still consumed.
    assert c.category == "devops"
    assert c.classifier_version == "stub-v1"


async def test_llm_classifier_falls_back_on_unparseable_output():
    fake = FakeLLMProvider(
        [LLMResult(text="not json at all", input_tokens=1, output_tokens=1, model_id="m")]
    )
    c = await LLMClassifier(fake).classify(SKILL_MD)
    assert c.classifier_version == "stub-v1"


async def test_llm_classifier_strips_code_fences():
    payload = json.dumps(
        {
            "category": "research",
            "tags": ["sql"],
            "quality_score": 80,
            "summary": "Run SQL.",
        }
    )
    fake = FakeLLMProvider(
        [LLMResult(text=f"```json\n{payload}\n```", input_tokens=1, output_tokens=1, model_id="m")]
    )
    c = await LLMClassifier(fake).classify("# Run SQL\nbody\n")
    assert c.category == "research"
    assert c.tags == ["sql"]


async def test_llm_classifier_normalizes_tags():
    fake = FakeLLMProvider(
        [
            _llm_result(
                {
                    "category": "creative",
                    "tags": ["React Hooks", "react hooks", "REACT-HOOKS", "  state  "],
                    "quality_score": 70,
                    "summary": "x",
                }
            )
        ]
    )
    c = await LLMClassifier(fake).classify("# body\n")
    # Deduped (case-insensitive after lowercase + space-to-hyphen) and trimmed.
    assert c.tags == ["react-hooks", "state"]
