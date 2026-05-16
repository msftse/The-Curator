"""M3 — Prompt templates for the curator LLM review pass.

Prompts are versioned constants. Bumping the version on a prompt change makes
historical proposals self-describing (every persisted ``ReviewProposal``
records the ``prompt_version`` it was produced under).

Pure module — no I/O. The AST gate scans this file (defense in depth).
"""

from __future__ import annotations

PROMPT_VERSION = "v1"


DRIFT_SYSTEM = (
    "You are a meticulous reviewer of agent skill definitions. "
    "Your job is to look at a single SKILL.md and decide whether it has "
    "drifted (deprecated tool refs, dead env vars, obvious typo clusters, "
    "broken markdown) or is fine as-is. "
    "Respond with strict JSON only. Do not include code fences."
)


DRIFT_USER_TEMPLATE = """\
Skill name: {name}
Skill version: {version}

Current SKILL.md:
---
{skill_md}
---

Decide: verdict ∈ {{"keep", "patch"}}.
If "patch", return a full replacement SKILL.md in patch_text (mode=full_replace).
Return JSON ONLY with this exact shape:
{{"verdict": "<keep|patch>", "patch_text": "<replacement SKILL.md or empty string>",
  "confidence": <0..1 float>, "rationale": "<one short sentence>"}}
"""


CONSOLIDATION_SYSTEM = (
    "You are a meticulous reviewer of agent skill definitions. "
    "Two skill bundles look similar by cheap token overlap. Decide whether "
    "they should be MERGED into a single umbrella skill, or KEPT separate. "
    "Respond with strict JSON only. Do not include code fences."
)


CONSOLIDATION_USER_TEMPLATE = """\
Skill A name: {a_name}
A SKILL.md:
---
{a_md}
---

Skill B name: {b_name}
B SKILL.md:
---
{b_md}
---

Decide: verdict ∈ {{"keep", "merge"}}.
If "merge", propose an umbrella SKILL.md combining both.
Return JSON ONLY with this exact shape:
{{"verdict": "<keep|merge>", "umbrella_name": "<short slug-friendly name>",
  "umbrella_skill_md": "<full umbrella SKILL.md or empty string>",
  "confidence": <0..1 float>, "rationale": "<one short sentence>"}}
"""
