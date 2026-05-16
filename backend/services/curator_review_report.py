"""M3 — Curator review report renderer (pure) + Blob persistence.

Sibling to ``backend/services/curator_report.py``. Writes to
``{curator_reports_container}/{curator_reviews_prefix}/{run_id}/`` so M2
report layout is untouched.

No mutations, no deletes; the AST gate scans this module for delete calls.
"""

from __future__ import annotations

from azure.storage.blob.aio import BlobServiceClient

from backend.core.config import Settings
from backend.models.review import CuratorReviewRunRecord, ReviewProposal


def render_review_report(
    rec: CuratorReviewRunRecord,
    proposals: list[ReviewProposal],
) -> str:
    lines: list[str] = []
    lines.append(f"# Curator Review Run {rec.run_id}")
    lines.append("")
    lines.append(f"- **Started:** {rec.started_at.isoformat()}")
    lines.append(f"- **Finished:** {rec.finished_at.isoformat()}")
    lines.append(f"- **Provider:** {rec.provider}")
    lines.append(f"- **Model:** {rec.model_id or '(unknown)'}")
    lines.append(f"- **Prompt version:** {rec.prompt_version}")
    lines.append(f"- **Aborted reason:** {rec.aborted_reason or '(none)'}")
    lines.append(f"- **Lock token:** {rec.lock_token or '(n/a)'}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- candidates considered: {rec.candidates_considered}")
    lines.append(f"- proposals emitted: {rec.proposals_emitted}")
    lines.append(f"- patch: {rec.proposals_by_kind.get('patch', 0)}")
    lines.append(f"- merge: {rec.proposals_by_kind.get('merge', 0)}")
    lines.append(f"- keep:  {rec.proposals_by_kind.get('keep', 0)}")
    lines.append(f"- input tokens:  {rec.total_input_tokens}")
    lines.append(f"- output tokens: {rec.total_output_tokens}")
    lines.append("")
    lines.append("## Proposals")
    if not proposals:
        lines.append("_None._")
    else:
        lines.append("| id | kind | status | targets | confidence |")
        lines.append("| --- | --- | --- | --- | --- |")
        for p in sorted(proposals, key=lambda x: (x.kind, x.id)):
            targets = ",".join(p.target_skill_ids)
            lines.append(
                f"| {p.id} | {p.kind} | {p.status} | {targets} | {p.confidence:.2f} |"
            )
    lines.append("")
    return "\n".join(lines)


async def persist_review_report(
    blob: BlobServiceClient,
    settings: Settings,
    rec: CuratorReviewRunRecord,
    proposals: list[ReviewProposal],
) -> None:
    container = blob.get_container_client(settings.curator_reports_container)
    prefix = f"{settings.curator_reviews_prefix}/{rec.run_id}"

    run_blob = container.get_blob_client(f"{prefix}/run.json")
    await run_blob.upload_blob(rec.model_dump_json().encode("utf-8"), overwrite=True)

    md = render_review_report(rec, proposals)
    md_blob = container.get_blob_client(f"{prefix}/REPORT.md")
    await md_blob.upload_blob(md.encode("utf-8"), overwrite=True)
