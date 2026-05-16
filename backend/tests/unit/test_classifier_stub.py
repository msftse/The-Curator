from __future__ import annotations

from backend.services.classifier_stub import StubClassifier, make_classifier

SKILL_MD = """---
name: foo-skill
description: does foo
category: devops
tags: [k8s, ops, deploy]
---
This is the first line that becomes the summary.

More body text below.
"""


def test_stub_uses_frontmatter():
    c = StubClassifier().classify(SKILL_MD)
    assert c.category == "devops"
    assert c.tags == ["k8s", "ops", "deploy"]
    assert c.quality_score == 70
    assert c.summary.startswith("This is the first line")
    assert c.classifier_version == "stub-v1"
    assert c.duplicate_candidates == []


def test_stub_deterministic():
    a = StubClassifier().classify(SKILL_MD)
    b = StubClassifier().classify(SKILL_MD)
    assert a.category == b.category
    assert a.tags == b.tags
    assert a.summary == b.summary


def test_stub_defaults_when_frontmatter_missing():
    c = StubClassifier().classify("# no frontmatter\nbody")
    assert c.category == "uncategorized"
    assert c.tags == []


def test_stub_tag_limit():
    md = (
        "---\nname: x\ndescription: y\ntags: ["
        + ",".join(f"t{i}" for i in range(20))
        + "]\n---\nbody\n"
    )
    c = StubClassifier().classify(md)
    assert len(c.tags) == 8


def test_make_classifier_unknown():
    import pytest

    with pytest.raises(ValueError):
        make_classifier("not-a-real-provider")


def test_make_classifier_stub():
    assert isinstance(make_classifier("stub"), StubClassifier)
