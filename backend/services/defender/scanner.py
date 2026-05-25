"""Defender scanner — LLM-only skill security review.

Two implementations, selected by ``Settings.defender_provider``:

* ``FoundryDefenderScanner`` — production. Calls Azure AI Foundry through
  the shared ``FoundryLLMProvider`` with structured output set to
  ``DefenderReport``. Auth resolution is identical to the classifier's
  LLM path (Managed Identity in cloud, ``AZURE_AI_FOUNDRY_API_KEY``
  locally).
* ``FakeDefenderScanner`` — deterministic test double. Returns the next
  canned report off a queue; if the queue is empty it derives a clean
  report from input length so tests can run without explicit setup.

The scanner takes the raw skill bundle bytes (`pending_bundle_b64` on the
skill doc), extracts files, concatenates them into a single LLM prompt,
and either returns a `DefenderReport` or raises `DefenderTooLarge` when
the concatenated content exceeds `Settings.defender_max_tokens_input`.

This module performs no Cosmos / Redis / Blob I/O. The AST never-delete
gate (`backend/tests/unit/test_never_delete_invariant.py`) lists it as a
guarded module so future edits inherit the same constraint.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import Settings
from backend.core.errors import LLMProviderError
from backend.models.defender import (
    DefenderFinding,
    DefenderReport,
    DefenderSeverity,
    TokenUsage,
)
from backend.services.defender.prompts import (
    DEFENDER_SYSTEM_PROMPT,
    build_user_prompt,
)
from backend.services.skill_bundle import extract_tar, looks_like_tar

log = logging.getLogger(__name__)


class DefenderTooLarge(Exception):
    """Raised when concatenated bundle exceeds the configured token budget.

    The worker maps this to ``defender_status=failed`` with a finding
    ``rule=skill.too_large`` so admins can decide whether to reject the
    skill as quarantine or have the contributor break it into parts.
    """

    def __init__(self, char_count: int, char_budget: int) -> None:
        super().__init__(
            f"bundle text exceeds defender token budget: {char_count} chars > "
            f"{char_budget} char_budget (~{char_budget // 4} tokens)"
        )
        self.char_count = char_count
        self.char_budget = char_budget


class DefenderScanner(Protocol):
    name: str

    async def scan(self, *, bundle_bytes: bytes) -> DefenderReport: ...


# ---------- helpers ----------------------------------------------------


def _materialize_files(bundle_bytes: bytes) -> dict[str, bytes]:
    """Best-effort: treat input as tar; fall back to a single SKILL.md."""
    if looks_like_tar(bundle_bytes):
        try:
            return extract_tar(bundle_bytes)
        except Exception:  # pragma: no cover — best effort
            return {"SKILL.md": bundle_bytes}
    return {"SKILL.md": bundle_bytes}


def _concat_bundle(files: dict[str, bytes]) -> str:
    """Concatenate every file with `===== <path> =====` headers.

    Binary files (non-utf8) are surfaced with a `[binary, N bytes]` marker
    so the LLM can still flag suspicious-looking artifacts without us
    shipping raw bytes into the prompt.
    """
    parts: list[str] = []
    for path in sorted(files):
        data = files[path]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = f"[binary, {len(data)} bytes]"
        parts.append(f"===== {path} =====\n{text}\n")
    return "\n".join(parts)


def _max_finding_severity(findings: Iterable[DefenderFinding]) -> DefenderSeverity:
    order = [
        DefenderSeverity.LOW,
        DefenderSeverity.MEDIUM,
        DefenderSeverity.HIGH,
        DefenderSeverity.CRITICAL,
    ]
    rank = {s: i for i, s in enumerate(order)}
    best = -1
    for f in findings:
        try:
            sev = DefenderSeverity(f.severity)
        except ValueError:
            continue
        if rank.get(sev, -1) > best:
            best = rank[sev]
    return order[best] if best >= 0 else DefenderSeverity.CLEAN


# ---------- Foundry-backed --------------------------------------------


# Strict shape the LLM is told to return. Distinct from DefenderReport so
# the worker controls model/scanned_at/duration/usage (the model never
# sees those).


class _LLMFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    severity: Literal["low", "medium", "high", "critical"]
    location: str = ""
    excerpt: str = Field(default="", max_length=400)
    explanation: str = ""


class _LLMReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_severity: Literal["clean", "low", "medium", "high", "critical"] = "clean"
    findings: list[_LLMFinding] = Field(default_factory=list)


class FoundryDefenderScanner:
    """Production scanner — Azure AI Foundry via the shared LLMProvider."""

    name = "foundry-v1"

    def __init__(self, settings: Settings, llm=None) -> None:
        self._settings = settings
        self._llm = llm  # lazy

    def _ensure_llm(self):
        if self._llm is not None:
            return self._llm
        # Lazy import — keeps the test-only path free of Azure SDK deps.
        from backend.services.llm.foundry import FoundryLLMProvider

        self._llm = FoundryLLMProvider(self._settings)
        return self._llm

    async def scan(self, *, bundle_bytes: bytes) -> DefenderReport:
        files = _materialize_files(bundle_bytes)
        bundle_text = _concat_bundle(files)

        char_budget = self._settings.defender_max_tokens_input * 4
        if len(bundle_text) > char_budget:
            raise DefenderTooLarge(len(bundle_text), char_budget)

        user_prompt = build_user_prompt(bundle_text)
        llm = self._ensure_llm()

        started = time.monotonic()
        try:
            result = await llm.complete(
                system=DEFENDER_SYSTEM_PROMPT,
                user=user_prompt,
                max_input_tokens=self._settings.defender_max_tokens_input,
                max_output_tokens=self._settings.defender_max_tokens_output,
                response_format=_LLMReport,
                temperature=0.0,
            )
        except LLMProviderError:
            raise

        duration_ms = int((time.monotonic() - started) * 1000)

        # Lenient parse — even with structured output, some Foundry
        # deployments wrap JSON in fences or return slightly off-schema
        # text. If we can't parse, raise LLMProviderError so the worker
        # marks the job failed (and the janitor re-queues).
        try:
            parsed = _LLMReport.model_validate_json(result.text)
        except Exception as exc:
            log.warning(
                "defender.unparseable_llm_output text_prefix=%r err=%s",
                result.text[:300],
                exc,
            )
            raise LLMProviderError(f"defender LLM returned unparseable JSON: {exc}") from exc

        findings = [
            DefenderFinding(
                rule=f.rule,
                severity=f.severity,
                location=f.location,
                excerpt=f.excerpt[:200],
                explanation=f.explanation,
            )
            for f in parsed.findings
        ]

        # Trust the LLM's overall_severity but clamp it: it MUST be ≥ the max
        # finding severity, and MUST be "clean" iff findings is empty.
        derived = _max_finding_severity(findings)
        claimed = DefenderSeverity(parsed.overall_severity)
        if not findings:
            overall = DefenderSeverity.CLEAN
        elif _severity_rank(claimed) < _severity_rank(derived):
            overall = derived
        else:
            overall = claimed

        return DefenderReport(
            overall_severity=overall,
            findings=findings,
            model=result.model_id,
            scanned_at=datetime.now(UTC),
            scan_duration_ms=duration_ms,
            token_usage=TokenUsage(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ),
        )


def _severity_rank(sev: DefenderSeverity) -> int:
    order = {
        DefenderSeverity.CLEAN: 0,
        DefenderSeverity.LOW: 1,
        DefenderSeverity.MEDIUM: 2,
        DefenderSeverity.HIGH: 3,
        DefenderSeverity.CRITICAL: 4,
    }
    return order[sev]


# ---------- Fake (tests) ----------------------------------------------


class FakeDefenderScanner:
    """Deterministic in-process scanner for tests.

    Construct with a list of canned ``DefenderReport`` instances; each
    ``scan()`` pops the next one. When the queue is exhausted, returns a
    fresh CLEAN report so tests that don't care about content still pass.
    Records every call's bundle byte length on ``self.calls``.
    """

    name = "fake-v1"

    def __init__(self, canned: Iterable[DefenderReport] | None = None) -> None:
        self._q: deque[DefenderReport] = deque(canned or [])
        self.calls: list[int] = []

    def extend(self, more: Iterable[DefenderReport]) -> None:
        self._q.extend(more)

    async def scan(self, *, bundle_bytes: bytes) -> DefenderReport:
        self.calls.append(len(bundle_bytes))
        if self._q:
            return self._q.popleft()
        return DefenderReport(
            overall_severity=DefenderSeverity.CLEAN,
            findings=[],
            model="fake-v1",
            scanned_at=datetime.now(UTC),
            scan_duration_ms=0,
            token_usage=TokenUsage(),
            notes="fake-default-clean",
        )


# ---------- factory ----------------------------------------------------


def make_scanner(provider: str, *, settings: Settings | None = None) -> DefenderScanner:
    if provider == "fake":
        return FakeDefenderScanner()
    if provider == "foundry":
        if settings is None:
            raise ValueError("make_scanner('foundry') requires settings=")
        return FoundryDefenderScanner(settings)
    raise ValueError(f"unknown defender provider: {provider!r}")
