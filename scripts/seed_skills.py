#!/usr/bin/env python
"""Seed the local stack with sample skills for manual testing.

Idempotent — re-running won't create duplicates.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import uuid
from datetime import UTC, datetime

from backend.core.config import get_settings
from backend.core.cosmos import (
    SKILLS_CONTAINER,
    ensure_containers,
    get_container,
    get_cosmos_client,
)
from backend.models.skill import (
    Bundle,
    Classification,
    SkillDoc,
)
from backend.services.skill_bundle import build_tar

SAMPLES = [
    {
        "name": "github-pr-workflow",
        "description": "Open a PR using gh CLI",
        "skill_md": """---
name: github-pr-workflow
description: Open a PR using gh CLI
category: github
tags: [git, pr]
---
# GitHub PR workflow

Use `gh pr create` after staging your commits.
""",
        "status": "pending",
    },
    {
        "name": "notion-meeting-notes",
        "description": "Summarize a meeting and post to Notion",
        "skill_md": """---
name: notion-meeting-notes
description: Summarize a meeting and post to Notion
category: productivity
tags: [notion, meetings]
---
# Notion meeting notes

Capture decisions, action items, owners.
""",
        "status": "classified",
    },
    {
        "name": "k8s-rollout-check",
        "description": "Verify a kubectl rollout succeeded",
        "skill_md": """---
name: k8s-rollout-check
description: Verify a kubectl rollout succeeded
category: devops
tags: [kubernetes, deploy]
---
# K8s rollout check

Run `kubectl rollout status` and surface failures.
""",
        "status": "approved",
    },
]


def _slug(name: str) -> str:
    from backend.services.skill_bundle import slugify

    return slugify(name)


async def main() -> int:
    settings = get_settings()
    client = get_cosmos_client(settings)
    try:
        db = await ensure_containers(client, settings.cosmos_db_name)
        skills = get_container(db, SKILLS_CONTAINER)
        for sample in SAMPLES:
            skill_id = _slug(sample["name"])
            # Idempotency: check for any doc with this skill_id.
            existing = [
                row
                async for row in skills.query_items(
                    query="SELECT TOP 1 * FROM c WHERE c.skill_id=@id",
                    parameters=[{"name": "@id", "value": skill_id}],
                    partition_key=skill_id,
                )
            ]
            if existing:
                print(f"skip {skill_id} (exists)")
                continue

            tar, checksum = build_tar({"SKILL.md": sample["skill_md"].encode("utf-8")})
            doc = SkillDoc(
                id=f"{skill_id}:1.0.0:{uuid.uuid4().hex[:8]}",
                skill_id=skill_id,
                version="1.0.0",
                name=sample["name"],
                description=sample["description"],
                status=sample["status"],
                classifier_status="done" if sample["status"] != "pending" else "queued",
                uploader="seed@org",
                skill_md_text=sample["skill_md"],
                pending_bundle_b64=base64.b64encode(tar).decode("ascii")
                if sample["status"] != "approved"
                else None,
            )
            if sample["status"] in ("classified", "approved"):
                doc.classification = Classification(
                    category="seed",
                    tags=["seed"],
                    quality_score=70,
                    summary=sample["description"],
                )
            if sample["status"] == "approved":
                doc.bundle = Bundle(
                    blob_url="http://localhost:10000/devstoreaccount1/published/"
                    f"{skill_id}/1.0.0/bundle.tar.gz",
                    checksum_sha256=checksum,
                    size_bytes=len(tar),
                    file_count=1,
                )
                doc.approved_at = datetime.now(UTC)
                doc.approver = "seed@org"
            await skills.create_item(body=doc.model_dump(mode="json"))
            print(f"seeded {skill_id} ({sample['status']})")
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
