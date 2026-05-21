"""Never-delete invariant — static AST gate (AGENTS.md §5).

Curator code MUST NOT call `*.delete_item(...)` anywhere, and MUST NOT call
`*.delete_blob(...)` outside the two specifically-allowed callsites:

  1. `move_published_to_archive` in `backend/services/curator.py` — the
     "archive = move, not copy" contract. Verified at runtime by
     `await dest.exists()` before the source delete.
  2. `move_to_deleted_after_retention` in
     `backend/services/quarantine_janitor.py` (M5-3) — the ONE
     delete-after-N-days code path in the system. Verified at runtime by
     checking `doc.quarantine_expires_at <= now` before the source
     delete.

Both are the verified-terminal end of their respective lifecycles.
Anywhere else, a `delete_blob` or `delete_item` is a hard test failure.

This is a code-level guard so regressions get caught even without
integration coverage.

How the scan works:

  1. Parse each guarded file into an AST.
  2. Walk every `Call` node. For each call whose callee is an `Attribute`
     access (`x.y(...)`):
       - if `attr == "delete_item"` → always an offense.
       - if `attr == "delete_blob"` → offense unless the enclosing
         function definition is one of the (path, fn_name) pairs in
         `_BLOB_DELETE_ALLOWED_SITES`.

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
    # M5-3 — Quarantine flow. Service has zero deletes (the staging bytes
    # live inline on the Cosmos doc and are cleared by a Cosmos write).
    # Janitor has ONE allowed delete callsite (see
    # `_BLOB_DELETE_ALLOWED_SITES` below).
    "backend/services/quarantine.py",
    "backend/services/quarantine_janitor.py",
    # M5-4 — Defender admin override. Cosmos-only flip + audit row; no
    # blob mutations, no Cosmos deletes.
    "backend/services/defender_override.py",
    # M5-5 — Notifier worker. Pure consumer; no Cosmos / Blob mutations
    # beyond an append-only audit row. Redis is queue + dedupe TTL +
    # recipient cache only (every key has a TTL).
    "backend/services/notifier/__init__.py",
    "backend/services/notifier/acs.py",
    "backend/services/notifier/graph.py",
    "backend/services/notifier/templates/__init__.py",
    "backend/workers/notifier.py",
]

# `delete_item` is *always* forbidden (Cosmos delete = data loss).
_FORBIDDEN_ALWAYS = {"delete_item"}

# `delete_blob` is forbidden EXCEPT inside these (relative_path, function_name)
# pairs. Each pair represents a verified-terminal lifecycle end:
#
#   - curator.move_published_to_archive: verifies `await dest.exists()`
#     on the archive container before deleting the source from
#     `published/`. "archive = move" (AGENTS.md §5).
#   - quarantine_janitor.move_to_deleted_after_retention: verifies the
#     skill doc's `quarantine_expires_at` is past `now` before deleting
#     the bundle blob from `quarantine/`. The ONE delete-after-N-days
#     code path in the system (AGENTS.md §5).
_BLOB_DELETE_ALLOWED_SITES: set[tuple[str, str]] = {
    ("backend/services/curator.py", "move_published_to_archive"),
    (
        "backend/services/quarantine_janitor.py",
        "move_to_deleted_after_retention",
    ),
}


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
            enclosing = _enclosing_function_name(node)
            if (rel, enclosing) in _BLOB_DELETE_ALLOWED_SITES:
                continue  # one of the verified-terminal allowed callsites
            offenders.append((node.lineno, attr, enclosing or "<module>"))

    assert not offenders, (
        f"{rel} contains forbidden delete calls (AGENTS.md §5 never-delete): "
        f"{offenders}. `delete_item` is forbidden everywhere; `delete_blob` "
        f"is allowed ONLY inside the verified-terminal callsites listed in "
        f"`_BLOB_DELETE_ALLOWED_SITES`."
    )


@pytest.mark.parametrize("allowed_file,allowed_fn", sorted(_BLOB_DELETE_ALLOWED_SITES))
def test_allowed_delete_blob_sites_actually_call_delete_blob(
    allowed_file: str, allowed_fn: str
) -> None:
    """Positive control: every whitelisted callsite must actually exist.

    Catches the regression where someone refactors away the verified-terminal
    semantics and reverts to copy-only / leave-forever. If this test fails,
    either the function was renamed (update `_BLOB_DELETE_ALLOWED_SITES`)
    or the contract was silently broken — re-read AGENTS.md §5.
    """
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
        f"(AGENTS.md §5 verified-terminal lifecycle end); none found. The "
        f"contract may have regressed — either the function was renamed or "
        f"the delete was removed entirely."
    )
