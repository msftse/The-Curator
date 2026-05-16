"""Classifier providers.

`ClassifierProvider` is the Protocol the worker depends on. Two implementations:

- ``StubClassifier`` — deterministic, frontmatter-driven. Default in dev and
  the only classifier the integration tests rely on. No network I/O.
- ``LLMClassifier`` — Azure AI Foundry-backed. Sees the full SKILL.md body
  and returns a structured ``Classification`` JSON via the existing
  ``LLMProvider`` abstraction. On any LLM error it falls back to the stub
  so an uploads-side LLM outage degrades gracefully (skill still classifies,
  just with worse metadata) instead of getting stuck in ``classifier_status=
  failed``.

The factory picks one via ``Settings.classifier_provider``: ``"stub"`` (default)
or ``"llm"``. Switching is config-only — the worker code is identical.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import Settings
from backend.core.errors import LLMProviderError
from backend.core.logging import get_logger
from backend.models.skill import CATEGORY_TAXONOMY, CATEGORY_UNCATEGORIZED, Classification
from backend.services.llm.provider import LLMProvider
from backend.services.skill_bundle import parse_skill_md

log = get_logger(__name__)


class _LLMClassification(BaseModel):
    """Strict shape returned by the LLM. Distinct from ``Classification``
    (the doc-level record) because the model never produces
    ``duplicate_candidates`` or ``classifier_version`` — those are set by
    the worker. Used as MAF ``response_format`` for server-side validation.
    """

    model_config = ConfigDict(extra="forbid")

    category: str = Field(description="One of the allowed categories.")
    tags: list[str] = Field(default_factory=list, max_length=8)
    quality_score: int = Field(ge=0, le=100, default=70)
    summary: str = Field(default="", max_length=200)


class ClassifierProvider(Protocol):
    name: str

    async def classify(self, skill_md_text: str) -> Classification: ...


# ---- Stub (deterministic) ----------------------------------------------


class StubClassifier:
    """Deterministic classifier — uses frontmatter when present, defaults otherwise.

    Output is identical for identical input, which keeps integration tests
    stable and makes the M0 demo loop predictable.
    """

    name = "stub-v1"

    async def classify(self, skill_md_text: str) -> Classification:
        return self._classify_sync(skill_md_text)

    @classmethod
    def _classify_sync(cls, skill_md_text: str) -> Classification:
        try:
            fm, body = parse_skill_md(skill_md_text)
        except Exception:
            fm, body = {}, skill_md_text or ""

        category = cls._coerce_str(fm.get("category")) or "uncategorized"
        tags = cls._coerce_tag_list(fm.get("tags"))
        summary = (body.strip().splitlines() or [""])[0][:140] if body else ""
        return Classification(
            category=category,
            tags=tags[:8],
            quality_score=70,
            summary=summary,
            duplicate_candidates=[],
            classifier_version=cls.name,
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


# ---- LLM-backed --------------------------------------------------------


# Curated allow-list. The LLM is instructed to pick one of these; anything else
# is coerced to "uncategorized" by the parser. Keeps the category facet usable
# in the catalog filter UI without an admin curation step. Sourced from the
# canonical taxonomy in backend.models.skill so the upload UI, classifier
# prompt, and validation never drift.
ALLOWED_CATEGORIES: tuple[str, ...] = CATEGORY_TAXONOMY + (CATEGORY_UNCATEGORIZED,)


_LLM_SYSTEM = (
    """\
You are the classifier for an internal catalog of agent skills. Each skill is a
SKILL.md file with optional YAML frontmatter and a Markdown body describing what
the skill does and when to use it.

Return ONLY a JSON object with this exact shape:
{
  "category": "<one of the allowed categories>",
  "tags": ["short-kebab-tag", ...],
  "quality_score": <integer 0-100>,
  "summary": "<one sentence, max 140 chars>"
}

Rules:
- `category` MUST be one of: """
    + ", ".join(ALLOWED_CATEGORIES)
    + """.
  If nothing fits, use "uncategorized".
- `tags` is 1-8 short, lower-case, hyphenated technology / domain terms drawn
  from the skill content (e.g. "kubernetes", "react", "sql"). No spaces, no
  emoji, no marketing words.
- `quality_score`: 80-100 only when the skill has a clear purpose, complete
  examples, and explicit usage instructions. 50-79 for partial / unclear docs.
  Below 50 only for empty or broken content.
- `summary`: one factual sentence, no first-person, no marketing.
- Output JSON only. No prose, no code fences, no commentary.
"""
)


_LLM_USER_TEMPLATE = """\
Classify this skill:

```
{body}
```
"""


class LLMClassifier:
    """Classifies via an injected ``LLMProvider``. Falls back to ``StubClassifier``
    on any LLM error so the upload pipeline never wedges on a flaky aux model.
    """

    name = "llm-v1"

    # Token budget — generous enough for a multi-page SKILL.md without
    # blowing the per-upload cost. The provider truncates client-side at the
    # char level when over budget.
    _MAX_INPUT_TOKENS = 4000
    _MAX_OUTPUT_TOKENS = 400

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def classify(self, skill_md_text: str) -> Classification:
        # Always parse frontmatter first — it's authoritative for category/tags
        # when the contributor took the trouble to fill it in. The LLM only
        # fills the gaps.
        try:
            fm, body = parse_skill_md(skill_md_text)
        except Exception:
            fm, body = {}, skill_md_text or ""

        frontmatter_category = StubClassifier._coerce_str(fm.get("category"))
        frontmatter_tags = StubClassifier._coerce_tag_list(fm.get("tags"))

        user_prompt = _LLM_USER_TEMPLATE.format(body=(body or skill_md_text or "").strip())

        log.info(
            "llm_classifier.invoke skill_chars=%d body_chars=%d "
            "frontmatter_category=%r frontmatter_tags=%r",
            len(skill_md_text or ""),
            len(body or ""),
            frontmatter_category,
            frontmatter_tags,
        )

        try:
            result = await self._llm.complete(
                system=_LLM_SYSTEM,
                user=user_prompt,
                max_input_tokens=self._MAX_INPUT_TOKENS,
                max_output_tokens=self._MAX_OUTPUT_TOKENS,
                response_format=_LLMClassification,
                temperature=0.0,
            )
        except LLMProviderError as exc:
            log.warning(
                "llm_classifier_provider_failed_falling_back_to_stub",
                extra={"err": str(exc)},
            )
            return StubClassifier._classify_sync(skill_md_text)

        # With Pydantic structured output, MAF returns text that should parse
        # cleanly. Keep the lenient JSON fallback for older providers (Fake,
        # any future Foundry deployment that ignores response_format) so a
        # malformed response still degrades to the stub instead of crashing.
        try:
            parsed_obj = _LLMClassification.model_validate_json(result.text)
            parsed = parsed_obj.model_dump()
        except Exception:
            parsed = _parse_llm_json(result.text)
            if parsed is None:
                log.warning(
                    "llm_classifier_unparseable_falling_back_to_stub",
                    extra={"raw_text": result.text[:500]},
                )
                return StubClassifier._classify_sync(skill_md_text)

        # Frontmatter wins over LLM when present — contributor intent is law.
        category = frontmatter_category or _coerce_category(parsed.get("category"))
        tags = frontmatter_tags or _coerce_tags(parsed.get("tags"))
        summary = _coerce_summary(parsed.get("summary"), body)
        quality = _coerce_quality(parsed.get("quality_score"))

        return Classification(
            category=category,
            tags=tags[:8],
            quality_score=quality,
            summary=summary,
            duplicate_candidates=[],
            classifier_version=f"{self.name}:{result.model_id}",
        )


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    # Be lenient — some models wrap JSON in fences despite the prompt.
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        # remove optional `json` language tag
        if candidate.startswith("json\n"):
            candidate = candidate[5:]
        candidate = candidate.strip("`").strip()
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _coerce_category(value: Any) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ALLOWED_CATEGORIES:
            return v
    return "uncategorized"


def _coerce_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in value:
        s = str(v).strip().lower().replace(" ", "-")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _coerce_summary(value: Any, fallback_body: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:140]
    if fallback_body:
        return (fallback_body.strip().splitlines() or [""])[0][:140]
    return ""


def _coerce_quality(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 70
    return max(0, min(100, n))


# ---- Factory -----------------------------------------------------------


def make_classifier(provider: str, *, settings: Settings | None = None) -> ClassifierProvider:
    if provider == "stub":
        return StubClassifier()
    if provider == "llm":
        if settings is None:
            raise ValueError("make_classifier('llm') requires settings=")
        # Lazy import — keeps the stub-only test path free of Azure SDK deps.
        from backend.services.llm.foundry import FoundryLLMProvider

        return LLMClassifier(FoundryLLMProvider(settings))
    raise ValueError(f"unknown classifier provider: {provider!r}")
