"""M3 — Curator LLM review pass (Azure AI Foundry, manager-approved).

This module sits on top of the M2 deterministic curator
(``backend/services/curator.py``). The deterministic pass handles usage-decay
(loaded → stale → archived). This pass examines the *content* of approved,
non-pinned, agent-created skills and emits three kinds of structured
proposals into the ``review_proposals`` Cosmos container:

* ``patch`` — drift detected; replacement SKILL.md proposed.
* ``merge`` — two skills look redundant; umbrella SKILL.md proposed.
* ``keep`` — explicit no-op verdict (recorded for auditability).

**This pass NEVER mutates skills directly.** Even archive-style outputs land
as proposals. Application requires manager approval via
``backend/services/curator_review_apply.py``.

Ordering / invariants:

1. Pause check (AGENTS.md §5 — operator intent always wins).
2. ``redis_lock(key_curator_run_lock(), ...)`` — same lock as the M2 pass,
   so the deterministic and review passes serialise instead of racing on
   the same skill ``_etag``. ``LockUnavailable`` → record with
   ``aborted_reason="lock"``, returned (not raised); review is opportunistic.
3. Candidate selection: ``status='approved' AND pinned=false AND
   STARTSWITH(uploader, prefix)`` ordered by load_count desc, limit
   ``curator_review_max_skills_per_run``.
4. For each candidate, read the SKILL.md from the published Blob bundle
   (NOT from ``SkillDoc.skill_md_text`` — Blob is source of truth for bytes).
5. Drift pass: one Foundry call per candidate. Cost guard accumulates tokens
   and aborts the loop if ``curator_review_max_total_tokens_per_run`` is
   exceeded (sets ``aborted_reason="cost_cap"``, returns normally).
6. Consolidation pass: cheap TF-IDF cosine pre-filter, then one Foundry call
   per surviving pair.
7. Persist each verdict to Cosmos as a ``ReviewProposal`` row (Cosmos-first
   ordering — AGENTS.md §4 rule #1). Persistence is incremental: partial
   runs are still useful to managers.
8. Persist a ``CuratorReviewRunRecord`` + Markdown report to Blob.

NEVER calls ``skills.delete_item(...)`` or ``published.delete_blob(...)``.
``backend/tests/unit/test_never_delete_invariant.py`` enforces this via a
static grep gate.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient

from backend.core.blob import published_blob_path
from backend.core.config import Settings
from backend.core.errors import LockUnavailable
from backend.core.logging import bind, get_logger
from backend.core.redis import (
    key_curator_run_lock,
    redis_lock,
)
from backend.models.review import (
    CuratorReviewRunRecord,
    KeepPayload,
    LLMUsage,
    MergePayload,
    PatchPayload,
    ReviewProposal,
)
from backend.services import curator_state
from backend.services.curator_review_prompts import (
    CONSOLIDATION_SYSTEM,
    CONSOLIDATION_USER_TEMPLATE,
    DRIFT_SYSTEM,
    DRIFT_USER_TEMPLATE,
    PROMPT_VERSION,
)
from backend.services.curator_review_similarity import top_similar_pairs
from backend.services.llm import LLMProvider, LLMProviderError

log = get_logger(__name__)


def _utc_iso_compact(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _input_hash(name: str, version: str, skill_md_text: str) -> str:
    h = hashlib.sha256()
    h.update(name.encode("utf-8"))
    h.update(b"\0")
    h.update(version.encode("utf-8"))
    h.update(b"\0")
    h.update(skill_md_text.encode("utf-8"))
    return h.hexdigest()


def _multi_input_hash(items: list[tuple[str, str, str]]) -> str:
    h = hashlib.sha256()
    for name, version, text in sorted(items, key=lambda t: (t[0], t[1])):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(version.encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def _extract_skill_md(tar_bytes: bytes) -> str | None:
    """Pull SKILL.md (any case) out of a published bundle tar.gz."""
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                base = member.name.rsplit("/", 1)[-1]
                if base.lower() == "skill.md":
                    f = tar.extractfile(member)
                    if f is None:
                        return None
                    return f.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    return None


async def _download_bundle(
    blob: BlobServiceClient,
    settings: Settings,
    *,
    skill_id: str,
    version: str,
) -> bytes | None:
    container = blob.get_container_client(settings.blob_published_container)
    client = container.get_blob_client(published_blob_path(skill_id, version))
    try:
        downloader = await client.download_blob()
        return await downloader.readall()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "review_bundle_missing",
            extra={"skill_id": skill_id, "version": version, "err": str(exc)},
        )
        return None


async def _load_candidates(
    skills: ContainerProxy,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Return raw Cosmos rows (we need ``_etag`` which Pydantic strips)."""
    query = (
        "SELECT * FROM c WHERE c.status='approved' AND c.pinned=false "
        "AND STARTSWITH(c.uploader, @prefix) "
        "ORDER BY c.usage.load_count DESC OFFSET 0 LIMIT @cap"
    )
    params = [
        {"name": "@prefix", "value": settings.curator_review_agent_uploader_prefix},
        {"name": "@cap", "value": int(settings.curator_review_max_skills_per_run)},
    ]
    out: list[dict[str, Any]] = []
    async for raw in skills.query_items(
        query=query,
        parameters=params,
    ):
        out.append(raw)
    return out


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


async def _persist_proposal(
    review_proposals: ContainerProxy,
    proposal: ReviewProposal,
) -> None:
    body = proposal.model_dump(mode="json")
    await review_proposals.create_item(body=body)


async def _persist_run_record(
    blob: BlobServiceClient,
    settings: Settings,
    record: CuratorReviewRunRecord,
    proposals: list[ReviewProposal],
) -> None:
    from backend.services import curator_review_report

    with contextlib.suppress(Exception):
        await curator_review_report.persist_review_report(
            blob, settings, record, proposals
        )


async def execute_review_pass(
    *,
    provider: LLMProvider,
    skills: ContainerProxy,
    audit: ContainerProxy,  # noqa: ARG001 — reserved; review run currently writes no audit row per skill
    review_proposals: ContainerProxy,
    system_state: ContainerProxy,
    blob: BlobServiceClient,
    redis: Any,
    settings: Settings,
    now: datetime | None = None,
    actor: str = "system:curator_review",
) -> CuratorReviewRunRecord:
    """Single review pass. Returns a run record; never raises on cost-cap.

    May raise ``CuratorPaused`` is intentionally NOT raised here — paused is
    returned as ``aborted_reason="paused"`` so the second cron job and the
    admin endpoint share semantics.
    """
    now = now or datetime.now(UTC)
    run_id = _utc_iso_compact(now)
    started_at = datetime.now(UTC)
    bind(actor=actor, run_id=run_id)

    record = CuratorReviewRunRecord(
        run_id=run_id,
        started_at=started_at,
        finished_at=started_at,  # placeholder; overwritten at the end
        provider=settings.curator_review_provider,
        prompt_version=PROMPT_VERSION,
    )
    persisted_proposals: list[ReviewProposal] = []

    if await curator_state.is_paused(system_state=system_state, redis=redis):
        record.aborted_reason = "paused"
        record.finished_at = datetime.now(UTC)
        await _persist_run_record(blob, settings, record, persisted_proposals)
        return record

    try:
        async with redis_lock(
            redis,
            key_curator_run_lock(),
            ttl=settings.curator_lock_ttl_seconds,
        ) as lock_token:
            record.lock_token = lock_token

            raw_candidates = await _load_candidates(skills, settings)
            record.candidates_considered = len(raw_candidates)

            # Resolve SKILL.md bytes for each candidate (skip-with-warn on miss).
            resolved: list[dict[str, Any]] = []
            for raw in raw_candidates:
                skill_id = raw.get("skill_id") or raw.get("id", "")
                version = raw.get("version", "unknown")
                name = raw.get("name", skill_id)
                etag = raw.get("_etag", "")
                bundle = await _download_bundle(
                    blob, settings, skill_id=skill_id, version=version
                )
                if bundle is None:
                    continue
                md = _extract_skill_md(bundle)
                if md is None:
                    log.warning(
                        "review_skill_md_missing",
                        extra={"skill_id": skill_id, "version": version},
                    )
                    continue
                resolved.append(
                    {
                        "skill_id": skill_id,
                        "version": version,
                        "name": name,
                        "etag": etag,
                        "skill_md": md,
                    }
                )

            # ---- Drift pass ----------------------------------------------
            drift_keep_ids: set[str] = set()
            cost_cap = int(settings.curator_review_max_total_tokens_per_run)
            cost_cap_hit = False

            for cand in resolved:
                if cost_cap_hit:
                    break
                user_prompt = DRIFT_USER_TEMPLATE.format(
                    name=cand["name"],
                    version=cand["version"],
                    skill_md=cand["skill_md"],
                )
                try:
                    result = await provider.complete(
                        system=DRIFT_SYSTEM,
                        user=user_prompt,
                        max_input_tokens=settings.curator_review_max_input_tokens,
                        max_output_tokens=settings.curator_review_max_output_tokens,
                        response_format="json_object",
                        temperature=0.0,
                    )
                except LLMProviderError as exc:
                    log.warning(
                        "review_drift_provider_error",
                        extra={"skill_id": cand["skill_id"], "err": str(exc)},
                    )
                    record.aborted_reason = "provider_error"
                    break

                record.total_input_tokens += result.input_tokens
                record.total_output_tokens += result.output_tokens
                record.model_id = record.model_id or result.model_id

                parsed = _parse_json_object(result.text)
                if parsed is None:
                    log.warning(
                        "review_drift_json_parse_failed",
                        extra={"skill_id": cand["skill_id"]},
                    )
                else:
                    verdict = str(parsed.get("verdict", "")).lower()
                    confidence = float(parsed.get("confidence", 0.0) or 0.0)
                    rationale = str(parsed.get("rationale", ""))
                    usage = LLMUsage(
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        model_id=result.model_id,
                        prompt_version=PROMPT_VERSION,
                    )
                    in_hash = _input_hash(cand["name"], cand["version"], cand["skill_md"])

                    if verdict == "patch":
                        proposal = ReviewProposal(
                            run_id=run_id,
                            kind="patch",
                            status="pending",
                            target_skill_ids=[cand["skill_id"]],
                            target_etags={cand["skill_id"]: cand["etag"]},
                            input_hash=in_hash,
                            patch=PatchPayload(
                                target_skill_id=cand["skill_id"],
                                target_version=cand["version"],
                                patch_text=str(parsed.get("patch_text", "")),
                                replacement_mode="full_replace",
                                rationale=rationale,
                            ),
                            usage=usage,
                            confidence=confidence,
                        )
                        await _persist_proposal(review_proposals, proposal)
                        persisted_proposals.append(proposal)
                        record.proposals_emitted += 1
                        record.proposals_by_kind["patch"] += 1
                    elif verdict == "keep":
                        proposal = ReviewProposal(
                            run_id=run_id,
                            kind="keep",
                            status="noop",
                            target_skill_ids=[cand["skill_id"]],
                            target_etags={cand["skill_id"]: cand["etag"]},
                            input_hash=in_hash,
                            keep=KeepPayload(
                                target_skill_id=cand["skill_id"],
                                rationale=rationale,
                            ),
                            usage=usage,
                            confidence=confidence,
                        )
                        await _persist_proposal(review_proposals, proposal)
                        persisted_proposals.append(proposal)
                        record.proposals_by_kind["keep"] += 1
                        drift_keep_ids.add(cand["skill_id"])
                    else:
                        log.warning(
                            "review_drift_unknown_verdict",
                            extra={"skill_id": cand["skill_id"], "verdict": verdict},
                        )

                if record.total_input_tokens + record.total_output_tokens > cost_cap:
                    cost_cap_hit = True
                    record.aborted_reason = "cost_cap"

            # ---- Consolidation pass --------------------------------------
            if not cost_cap_hit and record.aborted_reason is None:
                consolidation_pool = {
                    c["skill_id"]: c["skill_md"]
                    for c in resolved
                    if c["skill_id"] in drift_keep_ids
                }
                pairs = top_similar_pairs(
                    consolidation_pool,
                    min_cosine=settings.curator_review_consolidation_min_cosine,
                    max_pairs=settings.curator_review_consolidation_max_pairs,
                )
                cand_by_id = {c["skill_id"]: c for c in resolved}

                for a_id, b_id, _cos in pairs:
                    if cost_cap_hit:
                        break
                    a = cand_by_id[a_id]
                    b = cand_by_id[b_id]
                    user_prompt = CONSOLIDATION_USER_TEMPLATE.format(
                        a_name=a["name"],
                        a_md=a["skill_md"],
                        b_name=b["name"],
                        b_md=b["skill_md"],
                    )
                    try:
                        result = await provider.complete(
                            system=CONSOLIDATION_SYSTEM,
                            user=user_prompt,
                            max_input_tokens=settings.curator_review_max_input_tokens,
                            max_output_tokens=settings.curator_review_max_output_tokens,
                            response_format="json_object",
                            temperature=0.0,
                        )
                    except LLMProviderError as exc:
                        log.warning(
                            "review_consolidation_provider_error",
                            extra={"a": a_id, "b": b_id, "err": str(exc)},
                        )
                        record.aborted_reason = "provider_error"
                        break

                    record.total_input_tokens += result.input_tokens
                    record.total_output_tokens += result.output_tokens
                    record.model_id = record.model_id or result.model_id

                    parsed = _parse_json_object(result.text)
                    if parsed is not None:
                        verdict = str(parsed.get("verdict", "")).lower()
                        if verdict == "merge":
                            usage = LLMUsage(
                                input_tokens=result.input_tokens,
                                output_tokens=result.output_tokens,
                                model_id=result.model_id,
                                prompt_version=PROMPT_VERSION,
                            )
                            in_hash = _multi_input_hash(
                                [
                                    (a["name"], a["version"], a["skill_md"]),
                                    (b["name"], b["version"], b["skill_md"]),
                                ]
                            )
                            umbrella_md = str(parsed.get("umbrella_skill_md", "")).strip()
                            umbrella_name = str(
                                parsed.get("umbrella_name", f"umbrella-{a_id}-{b_id}")
                            )
                            if umbrella_md:
                                proposal = ReviewProposal(
                                    run_id=run_id,
                                    kind="merge",
                                    status="pending",
                                    target_skill_ids=[a_id, b_id],
                                    target_etags={a_id: a["etag"], b_id: b["etag"]},
                                    input_hash=in_hash,
                                    merge=MergePayload(
                                        merged_skill_ids=[a_id, b_id],
                                        proposed_umbrella_name=umbrella_name,
                                        proposed_umbrella_skill_md=umbrella_md,
                                        rationale=str(parsed.get("rationale", "")),
                                    ),
                                    usage=usage,
                                    confidence=float(
                                        parsed.get("confidence", 0.0) or 0.0
                                    ),
                                )
                                await _persist_proposal(review_proposals, proposal)
                                persisted_proposals.append(proposal)
                                record.proposals_emitted += 1
                                record.proposals_by_kind["merge"] += 1
                    if record.total_input_tokens + record.total_output_tokens > cost_cap:
                        cost_cap_hit = True
                        record.aborted_reason = "cost_cap"

    except LockUnavailable:
        record.aborted_reason = "lock"

    record.finished_at = datetime.now(UTC)
    await _persist_run_record(blob, settings, record, persisted_proposals)
    return record
