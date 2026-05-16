"""M3 — curator_review_apply.reject_proposal tests."""

from __future__ import annotations

import pytest

from backend.core.errors import ReviewProposalNotFound, ReviewProposalNotPending
from backend.models.review import KeepPayload, ReviewProposal
from backend.services import curator_review_apply as apply_svc


class _ProposalContainer:
    """Single-doc in-memory container keyed by id+partition."""

    def __init__(self, proposal: ReviewProposal | None = None) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        if proposal is not None:
            self.put(proposal)

    def put(self, p: ReviewProposal) -> None:
        self._items[(p.id, p.run_id)] = p.model_dump(mode="json")

    async def read_item(self, *, item: str, partition_key: str) -> dict:
        try:
            return dict(self._items[(item, partition_key)])
        except KeyError as exc:
            raise RuntimeError("not found") from exc

    async def replace_item(self, *, item: str, body: dict) -> dict:
        key = (item, body["run_id"])
        self._items[key] = dict(body)
        return dict(body)


class _AuditContainer:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def create_item(self, body):  # noqa: ANN001
        self.records.append(dict(body))
        return dict(body)


def _make_keep(status: str = "pending") -> ReviewProposal:
    return ReviewProposal(
        run_id="r1",
        kind="keep",
        status=status,
        target_skill_ids=["s1"],
        keep=KeepPayload(target_skill_id="s1", rationale="ok"),
    )


@pytest.mark.asyncio
async def test_reject_proposal_marks_rejected_and_audits():
    proposal = _make_keep("pending")
    rp = _ProposalContainer(proposal)
    audit = _AuditContainer()

    out = await apply_svc.reject_proposal(
        proposal_id=proposal.id,
        run_id="r1",
        actor="admin@example.com",
        reason="not useful",
        review_proposals=rp,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
    )
    assert out.status == "rejected"
    assert out.rejected_by == "admin@example.com"
    assert out.rejection_reason == "not useful"
    # Audit recorded with action review_reject.
    assert any(r.get("action") == "review_reject" for r in audit.records)


@pytest.mark.asyncio
async def test_reject_proposal_404_when_missing():
    rp = _ProposalContainer()
    audit = _AuditContainer()
    with pytest.raises(ReviewProposalNotFound):
        await apply_svc.reject_proposal(
            proposal_id="missing",
            run_id="r1",
            actor="admin@example.com",
            reason="x",
            review_proposals=rp,  # type: ignore[arg-type]
            audit=audit,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_reject_proposal_409_when_not_pending():
    proposal = _make_keep("rejected")
    rp = _ProposalContainer(proposal)
    audit = _AuditContainer()
    with pytest.raises(ReviewProposalNotPending):
        await apply_svc.reject_proposal(
            proposal_id=proposal.id,
            run_id="r1",
            actor="admin@example.com",
            reason="x",
            review_proposals=rp,  # type: ignore[arg-type]
            audit=audit,  # type: ignore[arg-type]
        )
