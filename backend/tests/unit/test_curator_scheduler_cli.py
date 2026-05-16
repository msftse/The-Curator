"""Unit tests for the curator_scheduler CLI surface (M4 Task 6).

These verify argparse behaviour and that the entrypoint dispatches correctly
between long-running and one-shot modes. We monkeypatch `run_forever` so
no Cosmos/Redis/Blob clients are constructed.
"""

from __future__ import annotations

import pytest

from backend.workers import curator_scheduler


def test_parse_args_defaults() -> None:
    args = curator_scheduler._parse_args([])
    assert args.once is False
    assert args.dry_run is False


def test_parse_args_once() -> None:
    args = curator_scheduler._parse_args(["--once"])
    assert args.once is True
    assert args.dry_run is False


def test_parse_args_once_dry_run() -> None:
    args = curator_scheduler._parse_args(["--once", "--dry-run"])
    assert args.once is True
    assert args.dry_run is True


def test_parse_args_dry_run_only() -> None:
    """--dry-run without --once is legal (dry-run loop)."""
    args = curator_scheduler._parse_args(["--dry-run"])
    assert args.once is False
    assert args.dry_run is True


def test_main_dispatches_once_and_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_forever(*, once: bool, dry_run: bool) -> int:
        captured["once"] = once
        captured["dry_run"] = dry_run
        return 0

    monkeypatch.setattr(curator_scheduler, "run_forever", fake_run_forever)

    rc = curator_scheduler.main(["--once", "--dry-run"])
    assert rc == 0
    assert captured == {"once": True, "dry_run": True}


def test_main_propagates_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_forever(*, once: bool, dry_run: bool) -> int:
        return 1

    monkeypatch.setattr(curator_scheduler, "run_forever", fake_run_forever)
    assert curator_scheduler.main(["--once"]) == 1


def test_main_loop_mode_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_forever(*, once: bool, dry_run: bool) -> int:
        captured["once"] = once
        captured["dry_run"] = dry_run
        return 0

    monkeypatch.setattr(curator_scheduler, "run_forever", fake_run_forever)
    rc = curator_scheduler.main([])
    assert rc == 0
    assert captured == {"once": False, "dry_run": False}
