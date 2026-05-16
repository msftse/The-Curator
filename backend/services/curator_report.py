"""Curator run-report rendering (pure functions for unit testability).

`render_report` returns a Markdown string. `persist_report` uploads both
`run.json` and `REPORT.md` to the `curator/` Blob container under
`{settings.curator_runs_container_prefix}/{run_id}/`.
"""

from __future__ import annotations

from collections import Counter

from azure.storage.blob.aio import BlobServiceClient

from backend.core.config import Settings
from backend.models.curator import CuratorRunRecord


def render_report(rec: CuratorRunRecord) -> str:
    lines: list[str] = []
    lines.append(f"# Curator Run {rec.run_id}")
    lines.append("")
    lines.append(f"- **Started:** {rec.started_at.isoformat()}")
    lines.append(f"- **Finished:** {rec.finished_at.isoformat()}")
    lines.append(f"- **Dry-run:** {rec.dry_run}")
    lines.append(f"- **Snapshot:** {rec.snapshot_name or '(none — dry-run)'}")
    lines.append(f"- **Lock token:** {rec.lock_token or '(n/a)'}")
    lines.append("")
    lines.append("## Planner inputs")
    for k in sorted(rec.planner_inputs):
        lines.append(f"- `{k}` = `{rec.planner_inputs[k]}`")
    lines.append("")

    # Summary by reason
    reason_counts = Counter(t.reason for t in rec.transitions)
    lines.append("## Summary")
    if not reason_counts:
        lines.append("_No transitions._")
    else:
        lines.append("| reason | count |")
        lines.append("| --- | --- |")
        for reason in sorted(reason_counts):
            lines.append(f"| {reason} | {reason_counts[reason]} |")
    lines.append("")

    # Detail table
    lines.append("## Transitions")
    if not rec.transitions:
        lines.append("_No transitions._")
    else:
        lines.append("| skill_id | version | before | after | reason | applied |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for t in sorted(rec.transitions, key=lambda x: x.skill_id):
            lines.append(
                f"| {t.skill_id} | {t.version} | {t.before} | {t.after} "
                f"| {t.reason} | {t.applied} |"
            )
    lines.append("")

    # Skipped pinned
    lines.append("## Skipped (pinned)")
    if not rec.skipped_pinned:
        lines.append("_None._")
    else:
        for sid in sorted(rec.skipped_pinned):
            lines.append(f"- {sid}")
    lines.append("")

    return "\n".join(lines)


async def persist_report(
    blob: BlobServiceClient,
    settings: Settings,
    rec: CuratorRunRecord,
) -> None:
    container = blob.get_container_client(settings.curator_reports_container)
    prefix = f"{settings.curator_runs_container_prefix}/{rec.run_id}"

    run_blob = container.get_blob_client(f"{prefix}/run.json")
    await run_blob.upload_blob(rec.model_dump_json().encode("utf-8"), overwrite=True)

    md = render_report(rec)
    md_blob = container.get_blob_client(f"{prefix}/REPORT.md")
    await md_blob.upload_blob(md.encode("utf-8"), overwrite=True)
