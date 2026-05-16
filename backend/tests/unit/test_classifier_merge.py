"""Merge policy for contributor-supplied category/tags + classifier output.

Spec (docs/PRD.md §7.2, AGENTS.md):
- `user_category`, when set, overrides the classifier's category outright.
- `tags = union(user_tags, classifier_tags)`, dedup case-insensitively,
  user-tag order first, capped at 8.

This logic lives in `backend.workers.classifier._merge_user_hints` so the
worker is the single integration point — every other write-path stays
ignorant of merging.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.models.skill import Classification
from backend.workers.classifier import _merge_user_hints


def _result(category: str = "uncategorized", tags: list[str] | None = None) -> Classification:
    return Classification(
        category=category,
        tags=list(tags or []),
        quality_score=70,
        summary="",
        classifier_version="stub-v1",
        classified_at=datetime.now(UTC),
    )


def test_user_category_overrides_classifier():
    merged = _merge_user_hints(
        _result(category="research"),
        user_category="devops",
        user_tags=[],
    )
    assert merged.category == "devops"


def test_empty_user_category_leaves_classifier_value():
    merged = _merge_user_hints(
        _result(category="research"),
        user_category=None,
        user_tags=[],
    )
    assert merged.category == "research"

    merged = _merge_user_hints(
        _result(category="research"),
        user_category="   ",
        user_tags=[],
    )
    assert merged.category == "research"


def test_user_tags_prepended_then_classifier_tags_appended():
    merged = _merge_user_hints(
        _result(tags=["kubernetes", "helm"]),
        user_category=None,
        user_tags=["urgent", "ops"],
    )
    assert merged.tags == ["urgent", "ops", "kubernetes", "helm"]


def test_dedup_is_case_insensitive_preserves_user_casing():
    merged = _merge_user_hints(
        _result(tags=["Kubernetes", "deploy"]),
        user_category=None,
        user_tags=["KUBERNETES", "ops"],
    )
    # First occurrence wins. "KUBERNETES" from user appears before "Kubernetes".
    assert merged.tags == ["KUBERNETES", "ops", "deploy"]


def test_total_tags_capped_at_eight():
    merged = _merge_user_hints(
        _result(tags=[f"c{i}" for i in range(10)]),
        user_category=None,
        user_tags=[f"u{i}" for i in range(5)],
    )
    assert len(merged.tags) == 8
    # User tags come first → all 5 user tags survive, plus the first 3
    # classifier tags.
    assert merged.tags[:5] == ["u0", "u1", "u2", "u3", "u4"]
    assert merged.tags[5:] == ["c0", "c1", "c2"]


def test_empty_and_whitespace_tags_dropped():
    merged = _merge_user_hints(
        _result(tags=["", "  ", "real"]),
        user_category=None,
        user_tags=["  ", "kept"],
    )
    assert merged.tags == ["kept", "real"]


def test_returns_new_object_does_not_mutate_input():
    original = _result(category="research", tags=["a"])
    merged = _merge_user_hints(original, user_category="devops", user_tags=["b"])
    assert original.category == "research"
    assert original.tags == ["a"]
    assert merged is not original
