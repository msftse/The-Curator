#!/usr/bin/env python
"""Bootstrap the local Azurite stack with all blob containers (M5).

Idempotent — re-running creates nothing new once containers exist. Provides
an explicit entrypoint for the M5 quarantine container so contributors
don't have to boot the full API just to materialize it.

Usage:
    docker compose up -d
    uv run python scripts/bootstrap_blob_containers.py

Reads connection info from `backend.core.config.Settings`, which defaults
to the Azurite well-known connection string when no `.env.local` overrides
are set. See AGENTS.md §3 + §6.
"""

from __future__ import annotations

import asyncio
import sys

from backend.core.blob import ensure_containers, get_blob_service
from backend.core.config import get_settings


async def _main() -> int:
    settings = get_settings()
    svc = get_blob_service(settings)
    try:
        await ensure_containers(svc, settings)
        # Sanity-list what's present now. `list_containers` is async-iter.
        names: list[str] = []
        async for c in svc.list_containers():
            names.append(c.name)
        names.sort()
        print(f"blob containers present: {names}")
        required = {
            settings.blob_published_container,
            settings.blob_archive_container,
            settings.blob_snapshots_container,
            settings.blob_quarantine_container,
        }
        missing = required - set(names)
        if missing:
            print(f"ERROR: missing required containers after bootstrap: {sorted(missing)}", file=sys.stderr)
            return 1
        return 0
    finally:
        await svc.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
