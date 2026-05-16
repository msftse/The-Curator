"""M3 — ReviewProposal model round-trip tests."""

from __future__ import annotations

from backend.models.review import (
    CuratorReviewRunRecord,
    KeepPayload,
    LLMUsage,
    MergePayload,
    PatchPayload,
    ReviewListResponse,
    ReviewProposal,
)


def _run_id() -> str:
    return "20260516T030000Z"


def test_patch_proposal_round_trip():
    p = ReviewProposal(
        run_id=_run_id(),
        kind="patch",
        target_skill_ids=["s1"],
        target_etags={"s1": "etag-1"},
        input_hash="abc",
        patch=PatchPayload(
            target_skill_id="s1",
            target_version="1.0.0",
            patch_text="---\nname: s1\ndescription: fixed\n---\nbody",
            rationale="typo fix",
        ),
        usage=LLMUsage(input_tokens=10, output_tokens=20, model_id="m"),
        confidence=0.9,
    )
    dumped = p.model_dump(mode="json")
    again = ReviewProposal.model_validate(dumped)
    assert again.kind == "patch"
    assert again.patch is not None
    assert again.patch.target_skill_id == "s1"
    assert again.target_etags == {"s1": "etag-1"}
    assert again.status == "pending"
    assert again.usage.input_tokens == 10


def test_merge_proposal_requires_two_ids():
    m = MergePayload(
        merged_skill_ids=["a", "b"],
        proposed_umbrella_name="umb",
        proposed_umbrella_skill_md="---\nname: umb\ndescription: d\n---\nbody",
    )
    assert m.proposed_umbrella_version == "1.0.0"
    p = ReviewProposal(
        run_id=_run_id(),
        kind="merge",
        target_skill_ids=["a", "b"],
        target_etags={"a": "e1", "b": "e2"},
        merge=m,
    )
    again = ReviewProposal.model_validate(p.model_dump(mode="json"))
    assert again.merge is not None
    assert len(again.merge.merged_skill_ids) == 2


def test_keep_proposal_default_status_noop_when_set():
    p = ReviewProposal(
        run_id=_run_id(),
        kind="keep",
        status="noop",
        target_skill_ids=["s1"],
        keep=KeepPayload(target_skill_id="s1", rationale="all good"),
    )
    again = ReviewProposal.model_validate(p.model_dump(mode="json"))
    assert again.status == "noop"
    assert again.keep is not None


def test_run_record_defaults():
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rec = CuratorReviewRunRecord(run_id="x", started_at=now, finished_at=now)
    assert rec.proposals_by_kind == {"patch": 0, "merge": 0, "keep": 0}
    assert rec.aborted_reason is None
    again = CuratorReviewRunRecord.model_validate(rec.model_dump(mode="json"))
    assert again.run_id == "x"


def test_review_list_response():
    rsp = ReviewListResponse(proposals=[], total=0)
    out = rsp.model_dump(mode="json")
    assert out == {"proposals": [], "total": 0}
