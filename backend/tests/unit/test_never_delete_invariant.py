"""Never-delete invariant — static AST gate (AGENTS.md §5).

Curator code MUST NOT call `*.delete_item(...)` or `*.delete_blob(...)`.
This is a code-level guard so regressions get caught even without
integration coverage.

We walk each guarded file's AST and look for `Call` nodes whose callee is
`Attribute(attr='delete_item')` or `Attribute(attr='delete_blob')`. This
ignores docstrings, comments, and string literals that happen to mention
the forbidden tokens.

Allow-list:
- `audit` deletes (we never delete those either, but the check is scoped to
  the skills + bundle code paths).
- Redis `delete(...)` calls — TTLed cache entries, not bytes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_GUARDED_FILES = [
    "backend/services/curator.py",
    "backend/services/curator_rollback.py",
    "backend/services/curator_state.py",
    "backend/services/curator_report.py",
    "backend/services/snapshot.py",
    "backend/services/usage.py",
    "backend/services/janitor.py",
    "backend/api/curator.py",
    "backend/workers/curator_scheduler.py",
    # M3 — curator LLM review modules.
    "backend/services/curator_review.py",
    "backend/services/curator_review_apply.py",
    "backend/services/curator_review_prompts.py",
    "backend/services/curator_review_similarity.py",
    "backend/services/curator_review_report.py",
    "backend/services/llm/provider.py",
    "backend/services/llm/foundry.py",
    "backend/services/llm/fake.py",
]

_FORBIDDEN_ATTRS = {"delete_item", "delete_blob"}


@pytest.mark.parametrize("rel", _GUARDED_FILES)
def test_no_forbidden_delete_calls(rel: str) -> None:
    path = _ROOT / rel
    assert path.exists(), f"guarded file missing: {rel}"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTRS:
            offenders.append((node.lineno, func.attr))
    assert not offenders, (
        f"{rel} contains forbidden delete calls (AGENTS.md §5 never-delete): "
        f"{offenders}. Use archive/move semantics instead."
    )
