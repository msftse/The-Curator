"""Optimistic-concurrency helpers for Cosmos writes.

The counter-increment pattern (used by the usage pipeline and the curator
executor) reads an item, mutates fields, and writes it back. Concurrent
writers can race the write — Cosmos signals this with HTTP 412 via
`CosmosAccessConditionFailedError` when we pass `etag=...` +
`match_condition=MatchConditions.IfNotModified`.

We retry up to N times. Beyond that the caller decides: usage ingest returns
503, the curator skips that doc and continues the pass.
"""

from __future__ import annotations

from typing import Any

from azure.core import MatchConditions
from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import ContainerProxy

_ETAG_FIELDS = ("_etag", "_rid", "_self", "_attachments", "_ts")


def _strip_system_fields(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _ETAG_FIELDS}


async def replace_with_etag_retry(
    container: ContainerProxy,
    *,
    item_id: str,
    partition_key: str,
    mutate,  # Callable[[dict], dict] - mutates the freshly-read body
    max_retries: int = 3,
) -> dict[str, Any]:
    """Read, apply `mutate`, replace with `if_match=etag`. Retry on 412.

    `mutate` is a pure function (or async function) taking the current Cosmos
    body and returning the new body. It is re-invoked on every retry so
    concurrent writers' updates are observed.
    """
    last_exc: Exception | None = None
    for _attempt in range(max_retries):
        raw = await container.read_item(item=item_id, partition_key=partition_key)
        etag = raw.get("_etag")
        body = _strip_system_fields(raw)
        new_body = mutate(body) if not _is_coroutine_fn(mutate) else await mutate(body)
        try:
            return await container.replace_item(
                item=item_id,
                body=new_body,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except cosmos_exc.CosmosAccessConditionFailedError as exc:
            last_exc = exc
            continue
    raise last_exc if last_exc else RuntimeError("replace_with_etag_retry exhausted retries")


def _is_coroutine_fn(fn) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)
