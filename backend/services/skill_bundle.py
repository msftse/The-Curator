"""SKILL.md bundle parsing + tar.gz packing helpers.

Pure functions only — no I/O, no global clients. Keeps the test surface tiny.
"""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from typing import Any

import yaml
from slugify import slugify as _slugify

from backend.core.errors import BundleTooLarge, InvalidBundle

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
REQUIRED_FRONTMATTER_KEYS = ("name", "description")


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from markdown body. Raises InvalidBundle on error."""
    if not text or not text.strip():
        raise InvalidBundle("SKILL.md is empty")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise InvalidBundle("SKILL.md missing YAML frontmatter (expected --- delimiters)")
    raw_fm, body = match.group(1), match.group(2)
    try:
        frontmatter = yaml.safe_load(raw_fm) or {}
    except yaml.YAMLError as exc:
        raise InvalidBundle(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise InvalidBundle("frontmatter must be a YAML mapping")
    missing = [k for k in REQUIRED_FRONTMATTER_KEYS if not frontmatter.get(k)]
    if missing:
        raise InvalidBundle(f"frontmatter missing required keys: {missing}")
    if not body.strip():
        raise InvalidBundle("SKILL.md body is empty")
    return frontmatter, body


def slugify(name: str) -> str:
    """Stable, URL-safe skill_id from a human name."""
    s = _slugify(name, lowercase=True, max_length=64) or "skill"
    return s


def enforce_size(data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise BundleTooLarge(
            f"bundle is {len(data)} bytes, max allowed is {max_bytes}",
            metadata={"size_bytes": len(data), "max_bytes": max_bytes},
        )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_tar(files: dict[str, bytes]) -> tuple[bytes, str]:
    """Build a deterministic gzipped tar from a {path: bytes} map.

    Deterministic = stable mtime, uid/gid 0, sorted entries — so the same input
    always produces the same checksum (M0 idempotent publish relies on this).
    """
    if not files:
        raise InvalidBundle("bundle has no files")
    buf = io.BytesIO()
    # mtime=0 + sorted iteration → deterministic bytes for the same input.
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
    out = buf.getvalue()
    return out, sha256_hex(out)


def extract_tar(data: bytes) -> dict[str, bytes]:
    """Inverse of build_tar — used by publish to re-pack the staged upload."""
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


def looks_like_tar(data: bytes) -> bool:
    """Cheap probe — does this bytes blob look like a (possibly gzipped) tar?"""
    if len(data) < 4:
        return False
    if data[:2] == b"\x1f\x8b":  # gzip magic
        return True
    return bool(len(data) >= 265 and data[257:262] == b"ustar")
