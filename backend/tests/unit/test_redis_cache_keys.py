from __future__ import annotations

from backend.core.redis import (
    key_cache_item,
    key_cache_list,
    key_lock_publish,
    key_queue_classifier,
)


def test_keys_are_colon_delimited():
    assert key_cache_list() == "cache:skills:list:v1"
    assert key_cache_item("abc") == "cache:skills:item:abc"
    assert key_queue_classifier() == "queue:classifier"
    assert key_lock_publish("abc") == "lock:publish:abc"


def test_keys_versioned_list_is_bumpable():
    # If we ever change list shape, bumping to v2 should be a one-line change.
    assert key_cache_list().endswith(":v1")
