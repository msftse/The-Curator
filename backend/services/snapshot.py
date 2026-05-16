"""Snapshot service.

Captures a deterministic tar.gz of every blob in the `published/` container,
plus a `manifest.json` describing what was captured. Used:
- by the curator executor before every real-run pass (rollback safety);
- by `curator_rollback` to restore Blob bytes byte-for-byte.

Snapshot tar bytes are reproducible for the same input — mtime=0, sorted
entries, mode 0o644 — same approach as `skill_bundle.build_tar`.

Retention: keep newest `settings.curator_snapshot_retention` snapshots.
Older ones are *moved* (copy bytes to `snapshots/_retired/{name}/`,
leave source as defense-in-depth) — never deleted (AGENTS.md §5).
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime

from azure.storage.blob.aio import BlobServiceClient

from backend.core.config import Settings
from backend.models.curator import SnapshotManifest, SnapshotManifestEntry


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_snapshot_tar(files: dict[str, bytes]) -> bytes:
    """Deterministic gzipped tar — same input → same bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for path in sorted(files.keys()):
            data = files[path]
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _extract_tar(data: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            out[member.name] = f.read()
    return out


def _utc_iso_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def snapshot_published(
    blob: BlobServiceClient,
    settings: Settings,
    *,
    run_id: str | None = None,
    prefix: str | None = None,
) -> SnapshotManifest:
    """Snapshot every blob in `published/`. Returns manifest, writes 2 blobs.

    Layout:
        {snapshots-container}/{folder}/skills.tar.gz
        {snapshots-container}/{folder}/manifest.json

    `folder` defaults to UTC-iso; `prefix` lets callers override (e.g. for
    `pre-rollback-{ts}` names).
    """
    if run_id is None:
        run_id = _utc_iso_compact()
    folder = prefix if prefix is not None else run_id

    published = blob.get_container_client(settings.blob_published_container)
    snapshots = blob.get_container_client(settings.blob_snapshots_container)

    files: dict[str, bytes] = {}
    entries: list[SnapshotManifestEntry] = []

    async for b in published.list_blobs():
        blob_client = published.get_blob_client(b.name)
        downloader = await blob_client.download_blob()
        data = await downloader.readall()
        files[b.name] = data
        # blob.name is "{skill_id}/{version}/bundle.tar.gz"
        parts = b.name.split("/")
        skill_id = parts[0] if parts else b.name
        version = parts[1] if len(parts) > 1 else "unknown"
        entries.append(
            SnapshotManifestEntry(
                skill_id=skill_id,
                version=version,
                status="approved",
                checksum_sha256=_sha256_hex(data),
                blob_path=b.name,
            )
        )

    tar_bytes = _build_snapshot_tar(files)
    manifest = SnapshotManifest(run_id=run_id, skills=entries)

    tar_blob = snapshots.get_blob_client(f"{folder}/skills.tar.gz")
    await tar_blob.upload_blob(tar_bytes, overwrite=True)

    manifest_blob = snapshots.get_blob_client(f"{folder}/manifest.json")
    await manifest_blob.upload_blob(
        manifest.model_dump_json().encode("utf-8"), overwrite=True
    )

    return manifest


async def list_snapshots(
    blob: BlobServiceClient, settings: Settings
) -> list[str]:
    """Top-level snapshot folder names sorted descending (newest first)."""
    snapshots = blob.get_container_client(settings.blob_snapshots_container)
    seen: set[str] = set()
    async for b in snapshots.list_blobs():
        top = b.name.split("/", 1)[0]
        if top == settings.curator_snapshots_retired_prefix:
            continue
        if top.startswith("pre-rollback-"):
            seen.add(top)
        else:
            seen.add(top)
    return sorted(seen, reverse=True)


async def load_manifest(
    blob: BlobServiceClient, settings: Settings, name: str
) -> SnapshotManifest:
    from backend.core.errors import SnapshotNotFound

    snapshots = blob.get_container_client(settings.blob_snapshots_container)
    client = snapshots.get_blob_client(f"{name}/manifest.json")
    try:
        downloader = await client.download_blob()
        raw = await downloader.readall()
    except Exception as exc:  # noqa: BLE001
        raise SnapshotNotFound(f"snapshot {name!r} not found") from exc
    return SnapshotManifest.model_validate(json.loads(raw))


async def download_snapshot_tar(
    blob: BlobServiceClient, settings: Settings, name: str
) -> bytes:
    snapshots = blob.get_container_client(settings.blob_snapshots_container)
    client = snapshots.get_blob_client(f"{name}/skills.tar.gz")
    downloader = await client.download_blob()
    return await downloader.readall()


def extract_snapshot_files(tar_bytes: bytes) -> dict[str, bytes]:
    """Public helper for callers (rollback) that need {blob_path: bytes}."""
    return _extract_tar(tar_bytes)


async def rotate_retention(
    blob: BlobServiceClient, settings: Settings
) -> list[str]:
    """Keep newest N; move older snapshots into `_retired/` (never delete).

    Returns the list of names that were rotated.
    """
    names = await list_snapshots(blob, settings)
    # Filter out `pre-rollback-*` from retention rotation — those are
    # operator-recovery aids and have their own lifecycle.
    primary = [n for n in names if not n.startswith("pre-rollback-")]
    keep = primary[: settings.curator_snapshot_retention]
    rotate = [n for n in primary if n not in keep]
    if not rotate:
        return []

    snapshots = blob.get_container_client(settings.blob_snapshots_container)
    for name in rotate:
        # Copy each blob under `{name}/...` to `{_retired}/{name}/...`.
        async for b in snapshots.list_blobs(name_starts_with=f"{name}/"):
            src = snapshots.get_blob_client(b.name)
            downloader = await src.download_blob()
            data = await downloader.readall()
            dest_name = f"{settings.curator_snapshots_retired_prefix}/{b.name}"
            dest = snapshots.get_blob_client(dest_name)
            await dest.upload_blob(data, overwrite=True)
        # Source intentionally NOT deleted (AGENTS.md §5 — never delete).
    return rotate
