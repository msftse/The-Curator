"""Tests for `replace_with_etag_retry` — optimistic concurrency retries."""

from __future__ import annotations

import pytest
from azure.core import MatchConditions
from azure.cosmos import exceptions as cosmos_exc

from backend.services.cosmos_helpers import replace_with_etag_retry


class _FakeContainer:
    """In-memory container honoring _etag if_match semantics."""

    def __init__(self, item: dict):
        self._item = dict(item)
        self.read_calls = 0
        self.replace_calls = 0
        self._etag_seq = 1
        self._item["_etag"] = f'"etag-{self._etag_seq}"'

    async def read_item(self, *, item, partition_key):  # noqa: ARG002
        self.read_calls += 1
        return dict(self._item)

    async def replace_item(
        self,
        *,
        item,  # noqa: ARG002
        body,
        etag=None,
        match_condition=None,
    ):
        self.replace_calls += 1
        if (
            match_condition == MatchConditions.IfNotModified
            and etag != self._item["_etag"]
        ):
            raise cosmos_exc.CosmosAccessConditionFailedError(
                status_code=412, message="precondition failed"
            )
        # accept; bump etag
        self._etag_seq += 1
        new_doc = dict(body)
        new_doc["_etag"] = f'"etag-{self._etag_seq}"'
        self._item = new_doc
        return dict(new_doc)


@pytest.mark.asyncio
async def test_etag_retry_succeeds_first_try():
    container = _FakeContainer({"id": "x", "skill_id": "x", "load_count": 0})

    def _mut(body):
        body["load_count"] += 1
        return body

    out = await replace_with_etag_retry(
        container, item_id="x", partition_key="x", mutate=_mut
    )
    assert out["load_count"] == 1
    assert container.replace_calls == 1
    assert container.read_calls == 1


@pytest.mark.asyncio
async def test_etag_retry_retries_on_412():
    container = _FakeContainer({"id": "x", "skill_id": "x", "load_count": 0})

    call_count = {"n": 0}
    real_replace = container.replace_item

    async def flaky_replace(*, item, body, etag=None, match_condition=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate a concurrent writer landing a write between our read
            # and our replace.
            container._etag_seq += 1
            container._item["_etag"] = f'"etag-{container._etag_seq}"'
            raise cosmos_exc.CosmosAccessConditionFailedError(
                status_code=412, message="lost the race"
            )
        return await real_replace(
            item=item, body=body, etag=etag, match_condition=match_condition
        )

    container.replace_item = flaky_replace  # type: ignore[method-assign]

    def _mut(body):
        body["load_count"] += 1
        return body

    out = await replace_with_etag_retry(
        container, item_id="x", partition_key="x", mutate=_mut, max_retries=3
    )
    assert out["load_count"] == 1
    assert container.read_calls >= 2  # retried


@pytest.mark.asyncio
async def test_etag_retry_gives_up_after_max_attempts():
    container = _FakeContainer({"id": "x", "skill_id": "x", "load_count": 0})

    async def always_412(*, item, body, etag=None, match_condition=None):  # noqa: ARG001
        raise cosmos_exc.CosmosAccessConditionFailedError(
            status_code=412, message="nope"
        )

    container.replace_item = always_412  # type: ignore[method-assign]

    def _mut(body):
        return body

    with pytest.raises(cosmos_exc.CosmosAccessConditionFailedError):
        await replace_with_etag_retry(
            container, item_id="x", partition_key="x", mutate=_mut, max_retries=2
        )
