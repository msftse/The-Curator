"""M3 — Pure-stdlib TF-IDF cosine similarity for the consolidation pre-filter.

Why stdlib? scikit-learn pulls in numpy + scipy for what amounts to ≤50 short
documents per run. The pre-filter runs once per review pass and is bounded
by ``curator_review_max_skills_per_run``; O(N²) over N≤50 is trivial.

Pure functions only — no I/O. The AST gate scans this file (defense in
depth) so future edits cannot sneak a delete call in.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def top_similar_pairs(
    docs: dict[str, str],
    *,
    min_cosine: float,
    max_pairs: int,
) -> list[tuple[str, str, float]]:
    """Return ``[(id_a, id_b, cosine), ...]`` sorted desc, filtered by ``min_cosine``.

    Empty / single-doc inputs return ``[]``.
    """
    if len(docs) < 2:
        return []

    tokens: dict[str, list[str]] = {k: _tokenize(v) for k, v in docs.items()}
    # Document frequency (one count per doc containing the term).
    df: Counter[str] = Counter()
    for toks in tokens.values():
        df.update(set(toks))
    n_docs = max(1, len(docs))
    idf: dict[str, float] = {
        w: math.log((1 + n_docs) / (1 + c)) + 1 for w, c in df.items()
    }

    vecs: dict[str, dict[str, float]] = {}
    for k, toks in tokens.items():
        if not toks:
            vecs[k] = {}
            continue
        tf = Counter(toks)
        v = {w: (tf[w] / len(toks)) * idf[w] for w in tf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs[k] = {w: x / norm for w, x in v.items()}

    keys = sorted(vecs.keys())
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = vecs[keys[i]], vecs[keys[j]]
            if not a or not b:
                continue
            short, long_ = (a, b) if len(a) <= len(b) else (b, a)
            cos = sum(short[w] * long_.get(w, 0.0) for w in short)
            if cos >= min_cosine:
                pairs.append((keys[i], keys[j], cos))

    pairs.sort(key=lambda t: (-t[2], t[0], t[1]))
    return pairs[:max_pairs]
