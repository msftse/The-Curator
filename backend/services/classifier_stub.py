"""Classifier providers.

`ClassifierProvider` is the Protocol the worker depends on. M0 ships
`StubClassifier` (deterministic). M3 will add `LLMClassifier` — swap the
factory in `make_classifier()` and nothing else changes.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.models.skill import Classification
from backend.services.skill_bundle import parse_skill_md


class ClassifierProvider(Protocol):
    name: str

    def classify(self, skill_md_text: str) -> Classification: ...


class StubClassifier:
    """Deterministic classifier — uses frontmatter when present, defaults otherwise.

    Output is identical for identical input, which keeps integration tests
    stable and makes the M0 demo loop predictable.
    """

    name = "stub-v1"

    def classify(self, skill_md_text: str) -> Classification:
        try:
            fm, body = parse_skill_md(skill_md_text)
        except Exception:
            fm, body = {}, skill_md_text or ""

        category = self._coerce_str(fm.get("category")) or "uncategorized"
        tags = self._coerce_tag_list(fm.get("tags"))
        summary = (body.strip().splitlines() or [""])[0][:140] if body else ""
        return Classification(
            category=category,
            tags=tags[:8],
            quality_score=70,
            summary=summary,
            duplicate_candidates=[],
            classifier_version=self.name,
        )

    @staticmethod
    def _coerce_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _coerce_tag_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        return []


def make_classifier(provider: str) -> ClassifierProvider:
    if provider == "stub":
        return StubClassifier()
    # M3: return LLMClassifier(...)
    raise ValueError(f"unknown classifier provider: {provider!r}")
