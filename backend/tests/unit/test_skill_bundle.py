from __future__ import annotations

import pytest

from backend.core.errors import BundleTooLarge, InvalidBundle
from backend.services.skill_bundle import (
    build_tar,
    enforce_size,
    extract_tar,
    parse_skill_md,
    sha256_hex,
    slugify,
)

VALID_MD = """---
name: my-skill
description: does a thing
---
# my-skill

Body text here.
"""


def test_parse_happy_path():
    fm, body = parse_skill_md(VALID_MD)
    assert fm["name"] == "my-skill"
    assert fm["description"] == "does a thing"
    assert "Body text here." in body


def test_parse_missing_frontmatter():
    with pytest.raises(InvalidBundle):
        parse_skill_md("# just a title\n")


def test_parse_missing_required_field():
    md = "---\nname: only-name\n---\nbody\n"
    with pytest.raises(InvalidBundle):
        parse_skill_md(md)


def test_parse_empty_body():
    md = "---\nname: foo\ndescription: bar\n---\n   \n"
    with pytest.raises(InvalidBundle):
        parse_skill_md(md)


def test_parse_invalid_yaml():
    md = "---\nname: foo\n  bad: : yaml\n---\nbody\n"
    with pytest.raises(InvalidBundle):
        parse_skill_md(md)


def test_slugify_stable():
    assert slugify("GitHub PR workflow") == "github-pr-workflow"
    assert slugify("") == "skill"


def test_enforce_size_ok():
    enforce_size(b"x" * 100, 1000)


def test_enforce_size_too_large():
    with pytest.raises(BundleTooLarge):
        enforce_size(b"x" * 1001, 1000)


def test_build_tar_deterministic():
    files = {"SKILL.md": b"hello", "references/note.md": b"world"}
    tar1, sum1 = build_tar(files)
    tar2, sum2 = build_tar(files)
    assert tar1 == tar2
    assert sum1 == sum2
    # checksum matches our computation
    assert sum1 == sha256_hex(tar1)


def test_build_tar_empty_raises():
    with pytest.raises(InvalidBundle):
        build_tar({})


def test_tar_roundtrip():
    files = {"SKILL.md": b"abc", "templates/t.md": b"def"}
    tar, _ = build_tar(files)
    extracted = extract_tar(tar)
    assert extracted == files
