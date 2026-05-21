"""Unit test for `scripts/bootstrap_blob_containers.py` (M5-1).

Pure unit test — no Azurite required. The script delegates to
`backend.core.blob.ensure_containers`, which we exercise against an
in-memory fake BlobServiceClient. Two guarantees:

1. The quarantine container is in the bootstrap set (regression guard
   against someone removing it).
2. `create_container()` is invoked idempotently — pre-existing containers
   raise `ResourceExistsError`, which the script must swallow.

An Azurite-backed integration variant belongs in `backend/tests/integration/`
and lands alongside the M5-3 quarantine janitor; until then this unit-level
gate is enough to catch the wiring regression M5-1 cares about.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.blob import ensure_containers
from backend.core.config import Settings


class _FakeContainerClient:
    def __init__(self, name: str, existing: set[str]) -> None:
        self.name = name
        self._existing = existing
        self.created = False

    async def create_container(self) -> None:
        if self.name in self._existing:
            # Mirror azure-sdk behavior: raises on conflict. The script
            # must swallow.
            raise RuntimeError(f"ContainerAlreadyExists: {self.name}")
        self._existing.add(self.name)
        self.created = True


class _FakeBlobService:
    def __init__(self, existing: set[str] | None = None) -> None:
        self._existing: set[str] = set(existing or set())
        self.handed_out: list[_FakeContainerClient] = []

    def get_container_client(self, name: str) -> _FakeContainerClient:
        c = _FakeContainerClient(name, self._existing)
        self.handed_out.append(c)
        return c

    async def close(self) -> None:  # parity with the real client
        return None


@pytest.mark.asyncio
async def test_ensure_containers_creates_quarantine_alongside_others() -> None:
    settings = Settings()
    svc = _FakeBlobService()
    await ensure_containers(svc, settings)  # type: ignore[arg-type]

    names_touched = {c.name for c in svc.handed_out}
    # All four blob containers + the curator-reports one must be on the
    # ensure list. Quarantine is the M5-1 deliverable; the rest are
    # regression guards.
    assert settings.blob_quarantine_container in names_touched
    assert settings.blob_published_container in names_touched
    assert settings.blob_archive_container in names_touched
    assert settings.blob_snapshots_container in names_touched
    # Every container was newly created.
    assert all(c.created for c in svc.handed_out)


@pytest.mark.asyncio
async def test_ensure_containers_is_idempotent() -> None:
    """Pre-existing containers must not raise."""
    settings = Settings()
    pre_existing = {
        settings.blob_published_container,
        settings.blob_quarantine_container,
    }
    svc = _FakeBlobService(existing=pre_existing)
    # Must not raise.
    await ensure_containers(svc, settings)  # type: ignore[arg-type]
    # Containers not in pre_existing were created; the rest left alone.
    by_name = {c.name: c for c in svc.handed_out}
    assert by_name[settings.blob_published_container].created is False
    assert by_name[settings.blob_quarantine_container].created is False
    assert by_name[settings.blob_archive_container].created is True


def test_bootstrap_script_module_loads_and_exposes_main() -> None:
    """Smoke-load the script as a module to catch syntax/import regressions.

    The script lives under `scripts/` (not a package) so we load it via
    importlib rather than `import scripts.bootstrap_blob_containers`.
    """
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "bootstrap_blob_containers.py"
    assert script.exists(), "scripts/bootstrap_blob_containers.py missing"
    spec = importlib.util.spec_from_file_location("_bootstrap_blob_containers", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bootstrap_blob_containers"] = mod
    try:
        spec.loader.exec_module(mod)
        assert hasattr(mod, "_main"), "script must expose async `_main()`"
    finally:
        sys.modules.pop("_bootstrap_blob_containers", None)


def test_bootstrap_script_uses_ensure_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end-ish: call the script's _main with patched blob deps and
    assert it invokes ensure_containers and surfaces success (rc=0).
    """
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "bootstrap_blob_containers.py"
    spec = importlib.util.spec_from_file_location("_bootstrap_blob_containers_e2e", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bootstrap_blob_containers_e2e"] = mod
    spec.loader.exec_module(mod)

    settings = Settings()

    class _ListResult:
        def __init__(self, names: list[str]) -> None:
            self._names = names

        def __aiter__(self):
            async def gen():
                for n in self._names:
                    obj = MagicMock()
                    obj.name = n
                    yield obj

            return gen()

    fake_svc = MagicMock()
    fake_svc.list_containers = MagicMock(
        return_value=_ListResult(
            [
                settings.blob_published_container,
                settings.blob_archive_container,
                settings.blob_snapshots_container,
                settings.blob_quarantine_container,
            ]
        )
    )
    fake_svc.close = AsyncMock()

    ensure_mock = AsyncMock()
    monkeypatch.setattr(mod, "get_blob_service", lambda _s: fake_svc)
    monkeypatch.setattr(mod, "ensure_containers", ensure_mock)
    monkeypatch.setattr(mod, "get_settings", lambda: settings)

    try:
        rc = asyncio.run(mod._main())
    finally:
        sys.modules.pop("_bootstrap_blob_containers_e2e", None)

    assert rc == 0
    ensure_mock.assert_awaited_once()
    fake_svc.close.assert_awaited_once()
