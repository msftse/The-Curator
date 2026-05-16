"""M3 — prompt template tests."""

from __future__ import annotations

from backend.services.curator_review_prompts import (
    CONSOLIDATION_SYSTEM,
    CONSOLIDATION_USER_TEMPLATE,
    DRIFT_SYSTEM,
    DRIFT_USER_TEMPLATE,
    PROMPT_VERSION,
)


def test_prompt_version_is_v1():
    assert PROMPT_VERSION == "v1"


def test_drift_system_mentions_json():
    assert "json" in DRIFT_SYSTEM.lower()


def test_consolidation_system_mentions_json():
    assert "json" in CONSOLIDATION_SYSTEM.lower()


def test_drift_template_formats():
    out = DRIFT_USER_TEMPLATE.format(
        name="My Skill",
        version="1.2.3",
        skill_md="Body here.",
    )
    assert "My Skill" in out
    assert "1.2.3" in out
    assert "Body here." in out
    # JSON shape hint must survive escaping.
    assert '"verdict"' in out
    assert '"patch_text"' in out


def test_consolidation_template_formats():
    out = CONSOLIDATION_USER_TEMPLATE.format(
        a_name="Alpha",
        a_md="A body",
        b_name="Beta",
        b_md="B body",
    )
    assert "Alpha" in out and "Beta" in out
    assert "A body" in out and "B body" in out
    assert '"verdict"' in out
    assert '"umbrella_name"' in out
