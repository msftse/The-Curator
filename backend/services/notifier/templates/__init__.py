"""Template loader + renderer for notifier emails (M5-5).

Stays on Python's built-in `str.format_map` to avoid pulling Jinja2 just
for eight short emails. Each event type has a pair of files in this
directory:

    {event_type}.txt    — plaintext body
    {event_type}.html   — HTML body

`event_type` is the dotted name from `backend.models.notifications`
(e.g. `skill.uploaded` → `skill.uploaded.txt`). Subjects live in
`SUBJECTS` below — same `str.format_map` semantics.

Missing payload keys default to empty strings (`_SafeDict`) so a
template never crashes the worker on a partial event. If you need
fail-loud rendering, render explicitly with `str.format(**payload)`.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TEMPLATE_DIR = Path(__file__).parent

# Pinned set of event types this directory knows how to render. Keep in
# lockstep with `backend/models/notifications.py:EventType`.
SUPPORTED_EVENT_TYPES: tuple[str, ...] = (
    "skill.uploaded",
    "skill.awaiting_review",
    "skill.quarantined",
    "skill.approved",
    "skill.rejected",
    "defender.flagged",
    "admin.override",
    "curator.weekly_report",
)

# Email subjects. Same `str.format_map` rendering as the bodies.
SUBJECTS: dict[str, str] = {
    "skill.uploaded": "[skillhub] New skill uploaded: {skill_name}",
    "skill.awaiting_review": "[skillhub] Skill awaiting review: {skill_name}",
    "skill.quarantined": "[skillhub] Skill quarantined: {skill_name}",
    "skill.approved": "[skillhub] Your skill was approved: {skill_name}",
    "skill.rejected": "[skillhub] Your skill was rejected: {skill_name}",
    "defender.flagged": "[skillhub] Defender flagged: {skill_name} ({severity})",
    "admin.override": "[skillhub] Admin override on {skill_name}",
    "curator.weekly_report": "[skillhub] Weekly curator report",
}


class _SafeDict(dict):
    """`str.format_map` mapping that returns '' for missing keys.

    Lets a producer omit optional payload fields without the worker
    blowing up. The template author is responsible for choosing keys
    that read sensibly when empty.
    """

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


class _SafeHtmlDict(_SafeDict):
    """HTML renderer mapping that escapes all user-controlled values."""

    def __getitem__(self, key: str) -> str:
        return html_lib.escape(str(super().__getitem__(key)), quote=True)


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    plain_text: str
    html: str


def _read(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def render_template(event_type: str, payload: dict[str, Any]) -> RenderedEmail:
    """Render `(subject, plaintext, html)` for an event type.

    Raises `KeyError` if `event_type` isn't one of `SUPPORTED_EVENT_TYPES`.
    Missing payload keys render as empty strings (see `_SafeDict`).
    """
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise KeyError(f"unsupported event type: {event_type!r}")

    safe = _SafeDict(payload)
    safe_html = _SafeHtmlDict(payload)
    subject = SUBJECTS[event_type].format_map(safe)
    text = _read(f"{event_type}.txt").format_map(safe)
    html = _read(f"{event_type}.html").format_map(safe_html)
    return RenderedEmail(subject=subject, plain_text=text, html=html)
