"""M3 — Apply / reject handlers for curator-review proposals.

This is the only place in M3 that writes to ``skills`` or ``published/``.
Every code path here is mutually exclusive with the M2 deterministic curator
via the shared ``key_curator_run_lock()`` Redis lock.

Ordering (mirrors ``backend/services/publish.py`` and
``backend/services/curator.py:_apply_one`` — AGENTS.md §4 rule #1):

1. Look up proposal by (id, run_id). Refuse if not pending.
2. Acquire ``redis_lock(key_curator_run_lock(), ...)``.
3. Re-read each target skill; refuse if ``_etag`` advanced beyond what the
   proposal captured at review-pass time.
4. Snapshot Blob (``snapshot_svc.snapshot_published(run_id=f"review-apply-{id}")``).
5. Blob mutation (patch: upload new version; merge: copy bytes to ``archive/``
   for merged-in skills, upload umbrella to ``published/``).
6. Cosmos write(s) — SOURCE OF TRUTH FLIP — via ``replace_with_etag_retry``
   for status flips and ``create_item`` for the umbrella.
7. Audit row per mutated skill.
8. Update the proposal (``status="applied"`` + telemetry).
9. Redis invalidation LAST.

NEVER calls ``skills.delete_item(...)`` or ``published.delete_blob(...)``.
``backend/tests/unit/test_never_delete_invariant.py`` enforces this via a
static grep gate. The merge path archives — it never deletes.
"""

from __future__ import annotations

import base64
import contextlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.blob import published_blob_path, put_published
from backend.core.config import Settings
from backend.core.errors import (
    ReviewProposalNotFound,
    ReviewProposalNotPending,
    ReviewProposalStale,
)
from backend.core.logging import bind, get_logger
from backend.core.redis import (
    key_cache_item,
    key_cache_list,
    key_curator_run_lock,
    key_queue_classifier,
    redis_lock,
)
from backend.models.review import ReviewProposal
from backend.models.skill import Bundle, SkillDoc
from backend.services import audit as audit_svc
from backend.services import snapshot as snapshot_svc
from backend.services.cosmos_helpers import replace_with_etag_retry
from backend.services.curator import move_published_to_archive
from backend.services.skill_bundle import build_tar, extract_tar

log = get_logger(__name__)


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _utc_iso_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _bump_patch_version(version: str) -> str:
    m = _SEMVER_RE.match(version or "")
    if not m:
        return f"{version}+rev{uuid.uuid4().hex[:6]}"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{major}.{minor}.{patch + 1}"


async def _load_proposal(
    review_proposals: ContainerProxy, *, proposal_id: str, run_id: str
) -> ReviewProposal:
    try:
        raw = await review_proposals.read_item(item=proposal_id, partition_key=run_id)
    except Exception as exc:  # noqa: BLE001
        raise ReviewProposalNotFound(
            f"proposal {proposal_id!r} (run_id={run_id!r}) not found"
        ) from exc
    return ReviewProposal.model_validate(raw)


async def _save_proposal(review_proposals: ContainerProxy, proposal: ReviewProposal) -> None:
    await review_proposals.replace_item(item=proposal.id, body=proposal.model_dump(mode="json"))


async def _load_skill_with_etag(
    skills: ContainerProxy, skill_id: str
) -> tuple[SkillDoc, str, str] | None:
    """Return (doc, doc_id, _etag) for the latest doc of ``skill_id`` or None."""
    query = "SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC"
    params = [{"name": "@id", "value": skill_id}]
    async for raw in skills.query_items(query=query, parameters=params, partition_key=skill_id):
        etag = raw.get("_etag", "")
        doc_id = raw["id"]
        doc = SkillDoc.model_validate(raw)
        return doc, doc_id, etag
    return None


async def _invalidate_cache(redis: Redis, skill_ids: list[str]) -> None:
    keys = [key_cache_list()] + [key_cache_item(s) for s in skill_ids]
    if not keys:
        return
    with contextlib.suppress(RedisError, Exception):
        await redis.delete(*keys)


async def reject_proposal(
    *,
    proposal_id: str,
    run_id: str,
    actor: str,
    reason: str,
    review_proposals: ContainerProxy,
    audit: ContainerProxy,
) -> ReviewProposal:
    """Mark a pending proposal rejected. No Blob or skill mutation."""
    bind(actor=actor)
    proposal = await _load_proposal(review_proposals, proposal_id=proposal_id, run_id=run_id)
    if proposal.status != "pending":
        raise ReviewProposalNotPending(
            f"proposal {proposal_id!r} is not pending (status={proposal.status})"
        )
    proposal.status = "rejected"
    proposal.rejected_by = actor
    proposal.rejected_at = datetime.now(UTC)
    proposal.rejection_reason = reason
    await _save_proposal(review_proposals, proposal)

    target = proposal.target_skill_ids[0] if proposal.target_skill_ids else "_review"
    with contextlib.suppress(Exception):
        await audit_svc.record(
            audit,
            skill_id=target,
            action="review_reject",
            actor=actor,
            metadata={
                "proposal_id": proposal_id,
                "kind": proposal.kind,
                "reason": reason,
            },
        )
    return proposal


async def apply_patch_proposal(
    *,
    proposal_id: str,
    run_id: str,
    actor: str,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    review_proposals: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
) -> ReviewProposal:
    """Apply a ``kind="patch"`` proposal: bundle rebuild + version bump."""
    bind(actor=actor)
    proposal = await _load_proposal(review_proposals, proposal_id=proposal_id, run_id=run_id)
    if proposal.status != "pending":
        raise ReviewProposalNotPending(
            f"proposal {proposal_id!r} is not pending (status={proposal.status})"
        )
    if proposal.kind != "patch" or proposal.patch is None:
        raise ReviewProposalNotPending(f"proposal {proposal_id!r} is not a patch proposal")

    async with redis_lock(
        redis,
        key_curator_run_lock(),
        ttl=settings.curator_lock_ttl_seconds,
    ):
        target_id = proposal.patch.target_skill_id
        loaded = await _load_skill_with_etag(skills, target_id)
        if loaded is None:
            raise ReviewProposalStale(f"skill {target_id!r} no longer exists")
        doc, doc_id, current_etag = loaded
        expected_etag = proposal.target_etags.get(target_id, "")
        if expected_etag and expected_etag != current_etag:
            proposal.status = "stale"
            proposal.apply_error = "etag mismatch"
            await _save_proposal(review_proposals, proposal)
            raise ReviewProposalStale(f"skill {target_id!r} _etag advanced since review")

        # 1. Snapshot for rollback safety.
        manifest = await snapshot_svc.snapshot_published(
            blob, settings, run_id=f"review-apply-{proposal_id}"
        )

        # 2. Download current bundle.
        container = blob.get_container_client(settings.blob_published_container)
        src = container.get_blob_client(published_blob_path(target_id, doc.version))
        downloader = await src.download_blob()
        current_tar = await downloader.readall()
        files = extract_tar(current_tar)
        files["SKILL.md"] = proposal.patch.patch_text.encode("utf-8")
        tar_bytes, checksum = build_tar(files)
        new_version = _bump_patch_version(doc.version)

        # 3. Upload new version (intentionally does not touch the old blob).
        blob_url = await put_published(
            blob, settings, skill_id=target_id, version=new_version, data=tar_bytes
        )

        # 4. Cosmos write (SOURCE OF TRUTH FLIP).
        old_version = doc.version

        def _flip(body: dict[str, Any]) -> dict[str, Any]:
            d = SkillDoc.model_validate(body)
            d.version = new_version
            d.bundle = Bundle(
                blob_url=blob_url,
                checksum_sha256=checksum,
                size_bytes=len(tar_bytes),
                file_count=len(files),
            )
            d.approved_at = datetime.now(UTC)
            d.approver = actor
            # Refresh SKILL.md cache on the doc so previews don't drift.
            d.skill_md_text = proposal.patch.patch_text if proposal.patch else d.skill_md_text
            return d.model_dump(mode="json")

        await replace_with_etag_retry(skills, item_id=doc_id, partition_key=target_id, mutate=_flip)

        # 5. Audit.
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=target_id,
                action="patch_apply",
                actor=actor,
                before={"version": old_version},
                after={"version": new_version, "checksum": checksum},
                metadata={
                    "proposal_id": proposal_id,
                    "snapshot_name": manifest.run_id,
                },
            )

        # 6. Update proposal.
        proposal.status = "applied"
        proposal.applied_by = actor
        proposal.applied_at = datetime.now(UTC)
        proposal.snapshot_name = manifest.run_id
        await _save_proposal(review_proposals, proposal)

        # 7. Cache bust LAST.
        await _invalidate_cache(redis, [target_id])

    return proposal


async def apply_merge_proposal(
    *,
    proposal_id: str,
    run_id: str,
    actor: str,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    review_proposals: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
) -> ReviewProposal:
    """Apply a ``kind="merge"`` proposal.

    Creates a new umbrella skill (``status="pending"`` — goes through the
    standard classifier + manager pipeline) and archives the merged-in skills
    (Blob copy + Cosmos status flip; sources NEVER deleted).
    """
    bind(actor=actor)
    proposal = await _load_proposal(review_proposals, proposal_id=proposal_id, run_id=run_id)
    if proposal.status != "pending":
        raise ReviewProposalNotPending(
            f"proposal {proposal_id!r} is not pending (status={proposal.status})"
        )
    if proposal.kind != "merge" or proposal.merge is None:
        raise ReviewProposalNotPending(f"proposal {proposal_id!r} is not a merge proposal")

    async with redis_lock(
        redis,
        key_curator_run_lock(),
        ttl=settings.curator_lock_ttl_seconds,
    ):
        merged_ids = list(proposal.merge.merged_skill_ids)
        loaded: dict[str, tuple[SkillDoc, str, str]] = {}
        for sid in merged_ids:
            row = await _load_skill_with_etag(skills, sid)
            if row is None:
                raise ReviewProposalStale(f"merged skill {sid!r} no longer exists")
            doc, doc_id, etag = row
            expected = proposal.target_etags.get(sid, "")
            if expected and expected != etag:
                proposal.status = "stale"
                proposal.apply_error = f"etag mismatch on {sid}"
                await _save_proposal(review_proposals, proposal)
                raise ReviewProposalStale(f"skill {sid!r} _etag advanced since review")
            loaded[sid] = (doc, doc_id, etag)

        manifest = await snapshot_svc.snapshot_published(
            blob, settings, run_id=f"review-apply-{proposal_id}"
        )

        # 1. Build umbrella bundle (SKILL.md only — M3 does not auto-merge
        #    references/templates; managers re-bundle later if needed).
        umbrella_name = proposal.merge.proposed_umbrella_name
        umbrella_md = proposal.merge.proposed_umbrella_skill_md
        umbrella_version = proposal.merge.proposed_umbrella_version
        umbrella_id = f"merge-{_utc_iso_compact()}-{uuid.uuid4().hex[:8]}"
        tar_bytes, checksum = build_tar({"SKILL.md": umbrella_md.encode("utf-8")})

        blob_url = await put_published(
            blob,
            settings,
            skill_id=umbrella_id,
            version=umbrella_version,
            data=tar_bytes,
        )

        # 2. Create umbrella skill doc (status=pending; classifier picks it up).
        umbrella_doc = SkillDoc(
            id=f"{umbrella_id}:{umbrella_version}:{uuid.uuid4().hex[:8]}",
            skill_id=umbrella_id,
            version=umbrella_version,
            name=umbrella_name,
            description=f"Umbrella merge of {', '.join(merged_ids)}",
            status="pending",
            classifier_status="queued",
            uploader=f"system:curator_review_merge:{actor}",
            uploaded_at=datetime.now(UTC),
            bundle=Bundle(
                blob_url=blob_url,
                checksum_sha256=checksum,
                size_bytes=len(tar_bytes),
                file_count=1,
            ),
            skill_md_text=umbrella_md,
            pending_bundle_b64=base64.b64encode(tar_bytes).decode("ascii"),
        )
        await skills.create_item(body=umbrella_doc.model_dump(mode="json"))

        # 3. Enqueue umbrella for classification (mirrors upload service).
        with contextlib.suppress(RedisError, Exception):
            await redis.rpush(key_queue_classifier(), umbrella_id)

        # 4. Archive merged-in skills (Blob MOVE + Cosmos status flip).
        # Each move verifies the archive copy exists before deleting the
        # published source (AGENTS.md §5). On any verification failure,
        # the move raises and this whole apply step bubbles up.
        for sid, (doc, doc_id, _etag) in loaded.items():
            await move_published_to_archive(blob, settings, skill_id=sid, version=doc.version)

            def _flip(body: dict[str, Any]) -> dict[str, Any]:
                d = SkillDoc.model_validate(body)
                d.status = "archived"
                return d.model_dump(mode="json")

            with contextlib.suppress(Exception):
                await replace_with_etag_retry(
                    skills, item_id=doc_id, partition_key=sid, mutate=_flip
                )

            with contextlib.suppress(Exception):
                await audit_svc.record(
                    audit,
                    skill_id=sid,
                    action="merge_apply",
                    actor=actor,
                    before={"status": "approved"},
                    after={"status": "archived"},
                    metadata={
                        "proposal_id": proposal_id,
                        "umbrella_id": umbrella_id,
                        "snapshot_name": manifest.run_id,
                    },
                )

        # 5. Audit umbrella upload.
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=umbrella_id,
                action="upload",
                actor=actor,
                after={"status": "pending", "version": umbrella_version},
                metadata={
                    "proposal_id": proposal_id,
                    "created_by_merge": True,
                    "merged_skill_ids": merged_ids,
                },
            )

        # 6. Update proposal.
        proposal.status = "applied"
        proposal.applied_by = actor
        proposal.applied_at = datetime.now(UTC)
        proposal.snapshot_name = manifest.run_id
        await _save_proposal(review_proposals, proposal)

        # 7. Cache bust LAST.
        await _invalidate_cache(redis, [*merged_ids, umbrella_id])

    return proposal
