"""Curator report rendering (pure)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.models.curator import CuratorRunRecord, Transition
from backend.services.curator_report import render_report


def _rec(transitions=None, skipped_pinned=None, dry_run=False):
    now = datetime(2026, 5, 16, tzinfo=UTC)
    return CuratorRunRecord(
        run_id="20260516T030000Z",
        started_at=now,
        finished_at=now,
        dry_run=dry_run,
        planner_inputs={"stale_days": 30, "archive_days": 90},
        transitions=transitions or [],
        skipped_pinned=skipped_pinned or [],
        snapshot_name="20260516T030000Z" if not dry_run else None,
    )


def test_render_empty_report():
    md = render_report(_rec(dry_run=True))
    assert "# Curator Run 20260516T030000Z" in md
    assert "Dry-run:** True" in md
    assert "_No transitions._" in md
    assert "_None._" in md


def test_render_with_transitions_and_pinned():
    transitions = [
        Transition(
            skill_id="b-skill",
            version="1.0.0",
            before="approved",
            after="stale",
            reason="stale_30d",
            applied=True,
        ),
        Transition(
            skill_id="a-skill",
            version="2.0.0",
            before="stale",
            after="archived",
            reason="archive_90d",
            applied=True,
        ),
    ]
    md = render_report(_rec(transitions=transitions, skipped_pinned=["pinned-1"]))
    # Summary table
    assert "| stale_30d | 1 |" in md
    assert "| archive_90d | 1 |" in md
    # Transitions sorted by skill_id
    a_idx = md.index("a-skill")
    b_idx = md.index("b-skill")
    assert a_idx < b_idx
    # Pinned listed
    assert "pinned-1" in md


def test_render_is_deterministic():
    r = _rec(
        transitions=[
            Transition(
                skill_id="x",
                version="1.0.0",
                before="approved",
                after="stale",
                reason="stale_30d",
                applied=True,
            ),
        ],
        skipped_pinned=["p1", "p2"],
    )
    assert render_report(r) == render_report(r)
