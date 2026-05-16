"""Curator service — deterministic planner + Cosmos-first executor.

Ordering for each archive transition (mirrors `backend/services/publish.py`,
which is the canonical example of AGENTS.md §4 rule #1):

1. **Blob mutation**: copy `published/{id}/{ver}/bundle.tar.gz` to
   `archive/{id}/{ver}/bundle.tar.gz`. We intentionally LEAVE the source
   (defense-in-depth — AGENTS.md §5). Catalog queries filter by
   `status='approved'` so archived skills disappear from the catalog
   regardless of source bytes still being present.
2. **Cosmos write** — SOURCE OF TRUTH FLIP — via `replace_with_etag_retry`.
3. **Audit write** (immutable row).
4. **Redis invalidation** — LAST, failures non-fatal (rule #2).

Stale transitions have no Blob mutation — only a Cosmos status flip plus
audit row plus cache invalidate.

Pinned skills are NEVER transitioned. The planner skips them upfront. The
executor re-reads each doc immediately before mutation; if `pinned` flipped
to True between planning and execution, the transition is silently dropped
(latest state wins).

NEVER calls `skills.delete_item(...)` or `published.delete_blob(...)`.
`backend/tests/unit/test_never_delete_invariant.py` enforces this via a
static grep gate.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.blob import published_blob_path
from backend.core.config import Settings
from backend.core.errors import (
    CuratorPaused,
    InvalidStatusTransition,
    SkillNotFound,
    SkillPinned,
)
from backend.core.logging import bind, get_logger
from backend.core.redis import (
    key_cache_item,
    key_cache_list,
    key_curator_run_lock,
    redis_lock,
)
from backend.models.curator import CuratorRunRecord, Transition, TransitionReason
from backend.models.skill import SkillDoc, SkillStatus
from backend.services import audit as audit_svc
from backend.services import curator_report, curator_state
from backend.services import snapshot as snapshot_svc
from backend.services.cosmos_helpers import replace_with_etag_retry

log = get_logger(__name__)


def _utc_iso_compact(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


# ---- Planner (pure) -----------------------------------------------------


_PLANNER_INPUT_STATUSES: set[SkillStatus] = {"approved", "stale"}


def plan_transitions(
    docs: list[SkillDoc],
    now: datetime,
    *,
    stale_days: int,
    archive_days: int,
) -> tuple[list[Transition], list[str]]:
    """Pure planner. Same input + same `now` → same output.

    Rules:
      - pinned → skipped (never emit a transition).
      - last_loaded_at within `stale_days` → steady-state (no transition).
      - last_loaded_at older than `archive_days` → archive.
      - last_loaded_at older than `stale_days` but newer than
        `archive_days` → stale.
      - last_loaded_at is None: grace period of `stale_days` from upload.
        After `archive_days` since upload with no loads → archive.
    """
    transitions: list[Transition] = []
    skipped: list[str] = []
    stale_cutoff = now - timedelta(days=stale_days)
    archive_cutoff = now - timedelta(days=archive_days)

    for doc in docs:
        if doc.status not in _PLANNER_INPUT_STATUSES:
            continue
        if doc.pinned:
            skipped.append(doc.skill_id)
            continue

        last = doc.usage.last_loaded_at
        reference = last if last is not None else doc.uploaded_at
        reason: TransitionReason
        after: SkillStatus | None = None

        if last is None:
            # Never been loaded — grace from upload.
            if doc.uploaded_at < archive_cutoff:
                after = "archived"
                reason = "archive_90d"
            else:
                # Still in grace; no transition.
                continue
        else:
            if reference >= stale_cutoff:
                # Recently loaded — steady state.
                continue
            elif reference >= archive_cutoff:
                if doc.status == "stale":
                    # Already stale, not yet archive-eligible — no change.
                    continue
                after = "stale"
                reason = "stale_30d"
            else:
                after = "archived"
                reason = "archive_90d"

        transitions.append(
            Transition(
                skill_id=doc.skill_id,
                version=doc.version,
                before=doc.status,
                after=after,
                reason=reason,
                applied=False,
            )
        )

    return transitions, skipped


# ---- Executor -----------------------------------------------------------


async def _load_candidate_docs(skills: ContainerProxy) -> list[SkillDoc]:
    query = "SELECT * FROM c WHERE c.status IN ('approved','stale')"
    out: list[SkillDoc] = []
    async for raw in skills.query_items(query=query):
        try:
            out.append(SkillDoc.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


async def copy_published_to_archive(
    blob: BlobServiceClient,
    settings: Settings,
    *,
    skill_id: str,
    version: str,
) -> None:
    """Copy a published bundle into the archive container (never deletes source).

    Public M3 surface — also called by ``curator_review_apply.apply_merge_proposal``.
    """
    src = blob.get_container_client(settings.blob_published_container).get_blob_client(
        published_blob_path(skill_id, version)
    )
    try:
        downloader = await src.download_blob()
        data = await downloader.readall()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "archive_copy_source_missing",
            extra={"skill_id": skill_id, "version": version, "err": str(exc)},
        )
        return
    dest = blob.get_container_client(settings.blob_archive_container).get_blob_client(
        published_blob_path(skill_id, version)
    )
    await dest.upload_blob(data, overwrite=True)


# Backward-compat alias for the prior private name.
_copy_to_archive = copy_published_to_archive


async def execute_pass(
    *,
    dry_run: bool,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
    system_state: ContainerProxy,
    settings: Settings,
    now: datetime | None = None,
    actor: str = "system:curator",
) -> CuratorRunRecord:
    """Run a single curator pass. Raises `CuratorPaused` / `LockUnavailable`."""
    now = now or datetime.now(UTC)
    run_id = _utc_iso_compact(now)
    started_at = datetime.now(UTC)
    bind(actor=actor)

    if await curator_state.is_paused(system_state=system_state, redis=redis):
        raise CuratorPaused("curator is paused; resume before running")

    async with redis_lock(
        redis,
        key_curator_run_lock(),
        ttl=settings.curator_lock_ttl_seconds,
    ) as lock_token:
        snapshot_name: str | None = None
        if not dry_run:
            manifest = await snapshot_svc.snapshot_published(blob, settings, run_id=run_id)
            snapshot_name = manifest.run_id

        candidate_docs = await _load_candidate_docs(skills)
        transitions, skipped = plan_transitions(
            candidate_docs,
            now,
            stale_days=settings.curator_stale_days,
            archive_days=settings.curator_archive_days,
        )

        applied_transitions: list[Transition] = []
        touched_skill_ids: list[str] = []

        if not dry_run:
            for t in sorted(transitions, key=lambda x: x.skill_id):
                ok = await _apply_one(
                    transition=t,
                    skills=skills,
                    audit=audit,
                    blob=blob,
                    settings=settings,
                    actor=actor,
                )
                t_applied = t.model_copy(update={"applied": ok})
                applied_transitions.append(t_applied)
                if ok:
                    touched_skill_ids.append(t.skill_id)

            # Cache invalidation LAST. Non-fatal.
            with contextlib.suppress(RedisError, Exception):
                keys = [key_cache_list()] + [key_cache_item(s) for s in touched_skill_ids]
                if keys:
                    await redis.delete(*keys)

            # Retention rotation (never deletes — moves to `_retired/`).
            with contextlib.suppress(Exception):
                await snapshot_svc.rotate_retention(blob, settings)
        else:
            applied_transitions = list(transitions)  # all applied=False

        finished_at = datetime.now(UTC)
        record = CuratorRunRecord(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            dry_run=dry_run,
            planner_inputs={
                "stale_days": settings.curator_stale_days,
                "archive_days": settings.curator_archive_days,
                "now": now.isoformat(),
            },
            transitions=applied_transitions,
            skipped_pinned=skipped,
            snapshot_name=snapshot_name,
            lock_token=lock_token,
        )

        with contextlib.suppress(Exception):
            await curator_report.persist_report(blob, settings, record)

        return record


async def _apply_one(
    *,
    transition: Transition,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    settings: Settings,
    actor: str,
) -> bool:
    """Apply a single transition. Returns True if mutation happened."""
    # Re-read latest doc for this skill_id; respect pin-after-plan + etag.
    rows = []
    query = "SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC"
    params = [{"name": "@id", "value": transition.skill_id}]
    async for raw in skills.query_items(
        query=query,
        parameters=params,
        partition_key=transition.skill_id,
    ):
        rows.append(raw)
        break
    if not rows:
        return False

    current = SkillDoc.model_validate(rows[0])
    if current.pinned:
        log.info("transition_skipped_pinned_after_plan", extra={"skill_id": current.skill_id})
        return False
    if current.status not in _PLANNER_INPUT_STATUSES:
        return False

    # 1. Blob mutation (archive only).
    if transition.after == "archived":
        await _copy_to_archive(
            blob,
            settings,
            skill_id=current.skill_id,
            version=current.version,
        )

    # 2. Cosmos write (SOURCE OF TRUTH FLIP).
    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.status = transition.after
        return d.model_dump(mode="json")

    try:
        await replace_with_etag_retry(
            skills,
            item_id=current.id,
            partition_key=current.skill_id,
            mutate=_flip,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "transition_cosmos_write_failed",
            extra={"skill_id": current.skill_id, "err": str(exc)},
        )
        return False

    # 3. Audit row.
    audit_action = "archive" if transition.after == "archived" else "stale"
    with contextlib.suppress(Exception):
        await audit_svc.record(
            audit,
            skill_id=current.skill_id,
            action=audit_action,
            actor=actor,
            before={"status": transition.before},
            after={"status": transition.after},
            metadata={"reason": transition.reason, "version": current.version},
        )

    return True


# ---- Admin manual archive ----------------------------------------------
#
# Admins can archive a single approved skill on demand. Same primitives as
# the deterministic curator pass (copy to archive/, flip Cosmos status,
# audit, invalidate cache). NEVER deletes — AGENTS.md §5 still holds.
#
# Differences from the curator pass:
#   - No snapshot. This is a single-skill op; rollback uses the existing
#     curator restore endpoint (POST /v1/admin/curator/restore/{id}) which
#     copies archive/→published/ and flips status back to approved.
#   - No run-lock. The publish lock on `skill_id` is not held either —
#     archive moves a *published* skill to archived; there's no concurrent
#     publish to race with (publish targets pending/classified).
#   - Refuses pinned skills with SkillPinned (operator must unpin first).
#   - Refuses non-approved skills with InvalidStatusTransition. Pending /
#     classified flow through reject; rejected / stale / archived have no
#     `published/` bytes to move.


async def archive_skill_now(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    reason: str,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
    settings: Settings,
) -> SkillDoc:
    """Admin-issued manual archive of a single approved skill.

    Raises:
      SkillNotFound: no doc for this skill_id.
      SkillPinned: skill is pinned; operator must unpin first.
      InvalidStatusTransition: skill is not in `approved` state.
    """
    bind(actor=actor, skill_id=skill_id)

    # Re-read latest doc (same pattern as `_apply_one`).
    rows: list[dict[str, Any]] = []
    async for raw in skills.query_items(
        query="SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC",
        parameters=[{"name": "@id", "value": skill_id}],
        partition_key=skill_id,
    ):
        rows.append(raw)
        break
    if not rows:
        raise SkillNotFound(f"skill {skill_id!r} not found")

    current = SkillDoc.model_validate(rows[0])

    if current.pinned:
        raise SkillPinned(
            f"skill {skill_id!r} is pinned; unpin before archiving",
            metadata={"pinned_by": current.pinned_by},
        )
    if current.status != "approved":
        raise InvalidStatusTransition(
            f"skill {skill_id!r} has status={current.status!r}; "
            f"admin archive only operates on 'approved' skills",
            metadata={"status": current.status},
        )

    # 1. Blob mutation — copy published → archive (leaves source for
    # defense-in-depth; catalog filters by status so archived skills
    # disappear from public listings regardless).
    await copy_published_to_archive(
        blob,
        settings,
        skill_id=current.skill_id,
        version=current.version,
    )

    # 2. Cosmos write — SOURCE OF TRUTH FLIP.
    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.status = "archived"
        return d.model_dump(mode="json")

    updated_raw = await replace_with_etag_retry(
        skills,
        item_id=current.id,
        partition_key=current.skill_id,
        mutate=_flip,
    )
    updated = SkillDoc.model_validate(updated_raw)

    # 3. Audit (never silently destroy — reason is required at the API layer).
    with contextlib.suppress(Exception):
        await audit_svc.record(
            audit,
            skill_id=current.skill_id,
            action="archive",
            actor=actor,
            actor_oid=actor_oid,
            before={"status": "approved"},
            after={"status": "archived"},
            metadata={
                "reason": reason,
                "source": "admin_manual",
                "version": current.version,
            },
        )

    # 4. Cache invalidation — LAST, non-fatal (rule #2).
    with contextlib.suppress(RedisError, Exception):
        await redis.delete(key_cache_list(), key_cache_item(current.skill_id))

    return updated
