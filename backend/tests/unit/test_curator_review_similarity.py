"""M3 — similarity helper tests (pure-stdlib TF-IDF)."""

from __future__ import annotations

from backend.services.curator_review_similarity import top_similar_pairs


def test_identical_docs_score_one():
    docs = {"a": "hello world hello", "b": "hello world hello"}
    pairs = top_similar_pairs(docs, max_pairs=10, min_cosine=0.0)
    assert pairs
    sid_a, sid_b, score = pairs[0]
    assert {sid_a, sid_b} == {"a", "b"}
    assert score > 0.99


def test_disjoint_docs_score_zero():
    docs = {"a": "alpha beta gamma", "b": "delta epsilon zeta"}
    pairs = top_similar_pairs(docs, max_pairs=10, min_cosine=0.0)
    # Either filtered out or score == 0
    if pairs:
        assert pairs[0][2] == 0.0


def test_min_cosine_filters():
    docs = {
        "a": "shared shared shared shared unique_a",
        "b": "shared shared shared shared unique_b",
        "c": "totally different words here please",
    }
    pairs_high = top_similar_pairs(docs, max_pairs=10, min_cosine=0.9)
    pairs_low = top_similar_pairs(docs, max_pairs=10, min_cosine=0.0)
    assert len(pairs_high) <= len(pairs_low)
    # a/b should beat threshold, a/c and b/c should not.
    for sid_a, sid_b, score in pairs_high:
        assert {sid_a, sid_b} == {"a", "b"}
        assert score >= 0.9


def test_top_k_limits():
    docs = {f"s{i}": f"common word s{i}_unique" for i in range(6)}
    pairs = top_similar_pairs(docs, max_pairs=3, min_cosine=0.0)
    assert len(pairs) <= 3


def test_single_doc_returns_empty():
    assert top_similar_pairs({"a": "hello"}, max_pairs=10, min_cosine=0.0) == []


def test_empty_docs_returns_empty():
    assert top_similar_pairs({}, max_pairs=10, min_cosine=0.0) == []


def test_pairs_sorted_descending():
    docs = {
        "a": "shared shared shared",
        "b": "shared shared shared",
        "c": "shared other",
        "d": "completely unrelated tokens",
    }
    pairs = top_similar_pairs(docs, max_pairs=10, min_cosine=0.0)
    scores = [s for _, _, s in pairs]
    assert scores == sorted(scores, reverse=True)
