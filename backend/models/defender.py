"""Defender domain models — LLM-based security scan report.

Spec: `.agents/plans/m5-defender-quarantine-notifier.md` §3.

Severity tiers (`DefenderSeverity`):
- ``clean``    — no findings. Skill proceeds to admin review queue normally.
- ``low``      — informational. Shown in review UI as warning; admin can approve
                 with no extra ceremony.
- ``medium``   — blocks one-click approve. Admin must provide a justification
                 (≥20 chars) on the approve call.
- ``high``     — same as medium + red banner in the UI. Admin can also choose
                 to reject → quarantine.
- ``critical`` — reserved for future auto-quarantine; today behaves like
                 ``high`` in the backend but the UI may render differently.

The worker maps the LLM's overall_severity (clean/low/medium/high) to the
skill doc's ``defender_status``: ``clean`` → ``clean``, everything else →
``flagged``. On scanner failure the worker sets ``failed``.

This module is import-only (no I/O), so it is exempt from the AST
never-delete gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DefenderSeverity(StrEnum):
    CLEAN = "clean"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Three-tier admin behavior per plan §3.
#   - "ok"                  — no admin friction; approve normally.
#   - "justification"       — admin must pass a justification ≥20 chars to approve.
#   - "justification_or_quarantine" — same as above, plus the quarantine
#     button is offered.
DefenderBehavior = Literal["ok", "justification", "justification_or_quarantine"]


_SEVERITY_BEHAVIOR: dict[DefenderSeverity, DefenderBehavior] = {
    DefenderSeverity.CLEAN: "ok",
    DefenderSeverity.LOW: "ok",
    DefenderSeverity.MEDIUM: "justification",
    DefenderSeverity.HIGH: "justification_or_quarantine",
    DefenderSeverity.CRITICAL: "justification_or_quarantine",
}


def severity_behavior(severity: DefenderSeverity | str) -> DefenderBehavior:
    """Map a severity tier to the admin-side behavior the backend enforces.

    Accepts either the enum or a plain string (LLM JSON path) for ergonomics.
    Unknown values default to the strictest behavior — fail safe.
    """
    try:
        sev = DefenderSeverity(severity)
    except ValueError:
        return "justification_or_quarantine"
    return _SEVERITY_BEHAVIOR[sev]


class DefenderFinding(BaseModel):
    """One discrete issue the scanner identified.

    Mirrors plan §3. ``severity`` here excludes ``clean`` — a clean finding
    is the absence of findings, not a row.
    """

    model_config = ConfigDict(extra="forbid")

    rule: str = Field(description="Short rule id, e.g. 'shell.dangerous_command'.")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="Per-finding severity. Overall_severity on the report is the max."
    )
    location: str = Field(description="Where the issue lives, e.g. 'scripts/setup.sh:42'.")
    excerpt: str = Field(
        default="",
        max_length=200,
        description="The offending snippet, truncated to 200 chars.",
    )
    explanation: str = Field(default="", description="LLM-written rationale.")


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class DefenderReport(BaseModel):
    """Structured output the Foundry scanner returns + worker persists.

    Stored inline on the skill doc (`SkillDoc.defender_report`). A future
    ``defender_reports`` Cosmos container could hold full versioned history;
    out of scope for M5-2.
    """

    model_config = ConfigDict(extra="forbid")

    overall_severity: DefenderSeverity = Field(
        default=DefenderSeverity.CLEAN,
        description="Max of finding severities, or 'clean' when findings is empty.",
    )
    findings: list[DefenderFinding] = Field(default_factory=list)
    model: str = Field(default="", description="Foundry model id / deployment used.")
    scanned_at: datetime = Field(default_factory=_utc_now)
    scan_duration_ms: int = 0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    # Free-text scanner-side note (e.g. "input truncated", "skill.too_large").
    notes: str = ""
