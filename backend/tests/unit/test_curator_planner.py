"""Pure planner tests — deterministic, same input + same now → same output.

No I/O. Covers AGENTS.md §5 never-delete invariant at the planning layer
(pinned skips, archived/stale only for `approved` + `stale` docs).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.models.skill import SkillDoc, UsageCounters
from backend.services.curator import plan_transitions

_NOW = datetime(2026, 5, 16, tzinfo=UTC)
_STALE_DAYS = 30
_ARCHIVE_DAYS = 90


def _doc(
    *,
    skill_id: str,
    status: str = "approved",
    pinned: bool = False,
    last_loaded_days_ago: int | None = None,
    uploaded_days_ago: int = 5,
) -> SkillDoc:
    last = _NOW - timedelta(days=last_loaded_days_ago) if last_loaded_days_ago is not None else None
    return SkillDoc(
        id=f"{skill_id}::1.0.0",
        skill_id=skill_id,
        version="1.0.0",
        name=skill_id,
        description="x",
        uploader="u@org",
        uploaded_at=_NOW - timedelta(days=uploaded_days_ago),
        status=status,  # type: ignore[arg-type]
        pinned=pinned,
        usage=UsageCounters(load_count=0, last_loaded_at=last, loaders_30d=0),
    )


def _plan(docs):
    return plan_transitions(docs, _NOW, stale_days=_STALE_DAYS, archive_days=_ARCHIVE_DAYS)


def test_pinned_never_transitions():
    docs = [_doc(skill_id="pinned", pinned=True, last_loaded_days_ago=1000)]
    transitions, skipped = _plan(docs)
    assert transitions == []
    assert skipped == ["pinned"]


def test_recent_load_steady_state():
    docs = [_doc(skill_id="hot", last_loaded_days_ago=5)]
    transitions, _ = _plan(docs)
    assert transitions == []


def test_stale_transition_at_31_days():
    docs = [_doc(skill_id="warming-down", last_loaded_days_ago=31)]
    transitions, _ = _plan(docs)
    assert len(transitions) == 1
    t = transitions[0]
    assert t.after == "stale"
    assert t.reason == "stale_30d"
    assert t.before == "approved"


def test_archive_transition_at_91_days():
    docs = [_doc(skill_id="cold", last_loaded_days_ago=91)]
    transitions, _ = _plan(docs)
    assert len(transitions) == 1
    t = transitions[0]
    assert t.after == "archived"
    assert t.reason == "archive_90d"


def test_stale_doc_not_re_archived_before_archive_window():
    docs = [_doc(skill_id="dormant", status="stale", last_loaded_days_ago=45)]
    transitions, _ = _plan(docs)
    assert transitions == []


def test_stale_doc_archived_after_archive_window():
    docs = [_doc(skill_id="cooling", status="stale", last_loaded_days_ago=95)]
    transitions, _ = _plan(docs)
    assert len(transitions) == 1
    assert transitions[0].after == "archived"
    assert transitions[0].before == "stale"


def test_never_loaded_within_grace_no_transition():
    docs = [_doc(skill_id="fresh", uploaded_days_ago=10, last_loaded_days_ago=None)]
    transitions, _ = _plan(docs)
    assert transitions == []


def test_never_loaded_after_archive_window_archived():
    docs = [_doc(skill_id="forgotten", uploaded_days_ago=95, last_loaded_days_ago=None)]
    transitions, _ = _plan(docs)
    assert len(transitions) == 1
    assert transitions[0].after == "archived"


def test_archived_doc_ignored():
    docs = [_doc(skill_id="gone", status="archived", last_loaded_days_ago=1000)]
    transitions, _ = _plan(docs)
    assert transitions == []


def test_planner_is_deterministic():
    docs = [
        _doc(skill_id="a", last_loaded_days_ago=5),
        _doc(skill_id="b", last_loaded_days_ago=35),
        _doc(skill_id="c", last_loaded_days_ago=100),
        _doc(skill_id="d", pinned=True, last_loaded_days_ago=200),
    ]
    r1 = _plan(docs)
    r2 = _plan(docs)
    assert r1 == r2
