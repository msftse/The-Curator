"""Never-delete invariant — static AST gate (AGENTS.md §5).

Curator code MUST NOT call `*.delete_item(...)` anywhere, and MUST NOT call
`*.delete_blob(...)` outside the body of `move_published_to_archive` in
`backend/services/curator.py`. The latter is the single allowed callsite —
"archive = move, not copy" — and it is guarded at runtime by an
`await dest.exists()` verification before the source delete.

This is a code-level guard so regressions get caught even without
integration coverage.

How the scan works:

  1. Parse each guarded file into an AST.
  2. Walk every `Call` node. For each call whose callee is an `Attribute`
     access (`x.y(...)`):
       - if `attr == "delete_item"` → always an offense.
       - if `attr == "delete_blob"` → offense unless the enclosing
         function definition is `move_published_to_archive` in
         `backend/services/curator.py`.

Enclosing function is computed by attaching parents to every node
(`ast` doesn't track parents natively) then walking up until we hit a
`FunctionDef` / `AsyncFunctionDef`.

Allow-list (intentional non-offenses):
- `audit` deletes — never written.
- Redis `delete(...)` — TTLed cache entries, not bytes.
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
    "backend/api/admin.py",
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
    # M4 — Kubernetes job dispatch (curator on-demand).
    "backend/services/k8s_jobs.py",
    # M5-2 — Defender (LLM security scanner). No Cosmos/Blob/Redis I/O in
    # the scanner; the worker writes Cosmos but never deletes.
    "backend/services/defender/__init__.py",
    "backend/services/defender/scanner.py",
    "backend/services/defender/prompts.py",
    "backend/workers/defender.py",
]

# M5 placeholder — the quarantine janitor (M5-3) will be the ONE allowed
# exception to the delete-blob prohibition outside `move_published_to_archive`,
# because quarantine is the only container where delete-after-N-days is
# permitted (AGENTS.md §5). The file does not exist yet; when M5-3 lands,
# move this entry from `_M5_PLACEHOLDER_FILES` into `_GUARDED_FILES` AND
# extend `_BLOB_DELETE_ALLOWED_SITES` with the new `(path, fn_name)` pair.
_M5_PLACEHOLDER_FILES = [
    "backend/services/quarantine_janitor.py",
]

# `delete_item` is *always* forbidden (Cosmos delete = data loss).
_FORBIDDEN_ALWAYS = {"delete_item"}

# `delete_blob` is forbidden EXCEPT inside this single function in this
# single file. The function verifies `await dest.exists()` immediately
# before calling `src.delete_blob()` (AGENTS.md §5 archive=move).
_BLOB_DELETE_ALLOWED = ("backend/services/curator.py", "move_published_to_archive")


def _attach_parents(tree: ast.AST) -> None:
    """Annotate each AST node with a `_parent` attribute pointing at its
    parent. Python's `ast` module doesn't expose parents otherwise."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def _enclosing_function_name(node: ast.AST) -> str | None:
    """Walk up via `_parent` until we hit a function definition. Returns
    the function name, or None if the node is at module scope."""
    current: ast.AST | None = getattr(node, "_parent", None)
    while current is not None:
        if isinstance(current, ast.AsyncFunctionDef | ast.FunctionDef):
            return current.name
        current = getattr(current, "_parent", None)
    return None


@pytest.mark.parametrize("rel", _GUARDED_FILES)
def test_no_forbidden_delete_calls(rel: str) -> None:
    path = _ROOT / rel
    assert path.exists(), f"guarded file missing: {rel}"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    _attach_parents(tree)

    offenders: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr = func.attr

        if attr in _FORBIDDEN_ALWAYS:
            offenders.append((node.lineno, attr, _enclosing_function_name(node) or "<module>"))
            continue

        if attr == "delete_blob":
            allowed_file, allowed_fn = _BLOB_DELETE_ALLOWED
            enclosing = _enclosing_function_name(node)
            if rel == allowed_file and enclosing == allowed_fn:
                continue  # the one allowed callsite
            offenders.append((node.lineno, attr, enclosing or "<module>"))

    assert not offenders, (
        f"{rel} contains forbidden delete calls (AGENTS.md §5 never-delete): "
        f"{offenders}. `delete_item` is forbidden everywhere; `delete_blob` "
        f"is allowed ONLY inside `move_published_to_archive` in "
        f"backend/services/curator.py."
    )


def test_move_published_to_archive_does_call_delete_blob() -> None:
    """Positive control: the one allowed callsite must actually exist.

    Catches the regression where someone refactors away the verified-move
    semantics and reverts to copy-only. If this test fails, either the
    function was renamed (update `_BLOB_DELETE_ALLOWED`) or the
    archive-as-move contract was silently broken — re-read AGENTS.md §5.
    """
    allowed_file, allowed_fn = _BLOB_DELETE_ALLOWED
    path = _ROOT / allowed_file
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    _attach_parents(tree)

    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "delete_blob":
            continue
        if _enclosing_function_name(node) == allowed_fn:
            found = True
            break
    assert found, (
        f"expected `delete_blob(...)` inside `{allowed_fn}` of {allowed_file} "
        f"(AGENTS.md §5 'archive = move'); none found. The verified-move "
        f"contract may have regressed to copy-only."
    )


def test_m5_quarantine_janitor_placeholder_documented() -> None:
    """Track the M5-3 quarantine janitor without forcing it to exist yet.

    AGENTS.md §5 carves quarantine out as the ONE container where
    delete-after-N-days is permitted. The janitor that performs that
    deletion will land in M5-3. This test is a structural reminder: when
    the file appears on disk, the gate above MUST be updated to include
    it in `_GUARDED_FILES` AND `_BLOB_DELETE_ALLOWED_SITES` (or whatever
    multi-site mechanism replaces the single-tuple constant), so the
    new file doesn't bypass the AST scan.
    """
    for rel in _M5_PLACEHOLDER_FILES:
        path = _ROOT / rel
        if path.exists():
            pytest.fail(
                f"{rel} now exists — promote it from _M5_PLACEHOLDER_FILES "
                f"into _GUARDED_FILES and add its single allowed delete callsite "
                f"to the blob-delete allowlist (mirroring `move_published_to_archive`)."
            )
