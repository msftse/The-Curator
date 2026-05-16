"""Unit tests for upload-form user_category / user_tags normalization.

Hits the pure helpers in `backend.services.upload`. Integration coverage
of the full `handle_upload` flow lives in `tests/integration/`.
"""

from __future__ import annotations

import pytest

from backend.core.errors import InvalidBundle
from backend.services.upload import _normalize_user_category, _normalize_user_tags

# ---- _normalize_user_category -----------------------------------------


def test_normalize_category_passes_valid_value():
    assert _normalize_user_category("devops") == "devops"
    assert _normalize_user_category("MLOps") == "mlops"
    assert _normalize_user_category("  github  ") == "github"


def test_normalize_category_treats_blank_as_none():
    assert _normalize_user_category(None) is None
    assert _normalize_user_category("") is None
    assert _normalize_user_category("   ") is None


def test_normalize_category_rejects_unknown():
    with pytest.raises(InvalidBundle, match="category must be one of"):
        _normalize_user_category("blockchain")


# ---- _normalize_user_tags ---------------------------------------------


def test_normalize_tags_trims_and_dedups_case_insensitive():
    assert _normalize_user_tags(["Helm", "helm", "K8s"]) == ["Helm", "K8s"]


def test_normalize_tags_caps_at_eight():
    raw = [f"tag-{i}" for i in range(20)]
    out = _normalize_user_tags(raw)
    assert len(out) == 8
    assert out == raw[:8]


def test_normalize_tags_drops_empty_strings():
    assert _normalize_user_tags(["", "  ", "real"]) == ["real"]


def test_normalize_tags_none_returns_empty_list():
    assert _normalize_user_tags(None) == []
    assert _normalize_user_tags([]) == []


def test_normalize_tags_rejects_over_long_tag():
    with pytest.raises(InvalidBundle, match="exceeds"):
        _normalize_user_tags(["x" * 41])
