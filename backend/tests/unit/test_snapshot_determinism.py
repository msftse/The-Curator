"""Snapshot determinism — same input → same bytes.

Exercises the private builder via the public `extract_snapshot_files` round-trip.
"""

from __future__ import annotations

from backend.services.snapshot import (
    _build_snapshot_tar,  # type: ignore[attr-defined] — testing internal helper
    extract_snapshot_files,
)


def test_build_tar_is_deterministic():
    files = {
        "alpha/1.0.0/bundle.tar.gz": b"alpha-bytes",
        "beta/2.0.0/bundle.tar.gz": b"beta-bytes",
        "gamma/0.1.0/bundle.tar.gz": b"\x00\x01\x02",
    }
    a = _build_snapshot_tar(files)
    b = _build_snapshot_tar(files)
    assert a == b


def test_build_tar_round_trips():
    files = {
        "x/1.0.0/bundle.tar.gz": b"hello",
        "y/2.0.0/bundle.tar.gz": b"world",
    }
    tar = _build_snapshot_tar(files)
    out = extract_snapshot_files(tar)
    assert out == files


def test_build_tar_order_independent():
    a = _build_snapshot_tar({"a": b"1", "b": b"2", "c": b"3"})
    b = _build_snapshot_tar({"c": b"3", "a": b"1", "b": b"2"})
    assert a == b


def test_build_tar_empty():
    tar = _build_snapshot_tar({})
    out = extract_snapshot_files(tar)
    assert out == {}
