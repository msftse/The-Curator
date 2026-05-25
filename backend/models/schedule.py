"""Curator schedule model (M5-7).

Single Cosmos document in the `system_state` container that drives the
curator CronJob's `.spec.schedule`. A small reconciler worker watches this
doc and patches the K8s CronJob when it changes.

Storage shape (Cosmos `system_state`, partition key `/key`):

    {
      "id": "curator_schedule",
      "key": "curator_schedule",
      "cron": "0 3 * * 0",
      "timezone": "UTC",
      "enabled": true,
      "updated_by": "alice@org",
      "updated_at": "2026-05-21T11:00:00+00:00"
    }

The default — applied when no doc exists — is **weekly Sunday 03:00 UTC**
(`0 3 * * 0`).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DEFAULT_CRON = "0 3 * * 0"  # Sunday 03:00 UTC
DEFAULT_TIMEZONE = "UTC"

# 5-field cron: minute, hour, day-of-month, month, day-of-week.
# Each field allows a small expression subset: `*`, integers, `a-b` ranges,
# `*/N` and `a-b/N` steps, and comma-separated lists of any of the above.
# Named months / weekdays are intentionally NOT supported — keeps the
# validator local-only (no croniter dep) and matches what the CronJob
# spec accepts in practice.
_FIELD = r"(\*|\d+(-\d+)?)(/\d+)?(,(\*|\d+(-\d+)?)(/\d+)?)*"
_CRON_RE = re.compile(rf"^{_FIELD}\s+{_FIELD}\s+{_FIELD}\s+{_FIELD}\s+{_FIELD}$")

# Field bounds — `(min, max)` inclusive. Matches K8s CronJob spec.
_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day-of-month
    (1, 12),  # month
    (0, 6),  # day-of-week (0=Sunday)
)


def validate_cron(expr: str) -> str:
    """Return the trimmed expression iff it parses, else raise ``ValueError``.

    The check is structural — it does NOT verify the schedule is satisfiable
    (e.g. `0 0 31 2 *` parses but never fires). K8s `kubectl create cronjob`
    accepts the same set.
    """
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("cron expression cannot be empty")
    # K8s also accepts a couple of shorthands (`@hourly`, `@daily`, …) but
    # the UI emits 5-field forms exclusively; rejecting `@`-forms keeps the
    # reconciler diff comparison trivial (string equality).
    if expr.startswith("@"):
        raise ValueError("@-shorthand cron expressions are not supported; use 5-field form")
    if not _CRON_RE.match(expr):
        raise ValueError(f"cron expression {expr!r} is not a valid 5-field cron")
    fields = expr.split()
    for raw, (lo, hi) in zip(fields, _BOUNDS, strict=True):
        for piece in raw.split(","):
            value = piece.split("/", 1)[0]
            if value == "*":
                continue
            if "-" in value:
                a_str, b_str = value.split("-", 1)
                a, b = int(a_str), int(b_str)
                if a < lo or b > hi or a > b:
                    raise ValueError(
                        f"cron field {raw!r} out of range [{lo},{hi}]"
                    )
            else:
                n = int(value)
                if n < lo or n > hi:
                    raise ValueError(
                        f"cron field {raw!r} out of range [{lo},{hi}]"
                    )
    return expr


class CuratorSchedule(BaseModel):
    """The single curator-schedule Cosmos doc.

    The reconciler diffs `cron` against the live CronJob spec; a difference
    triggers a patch. `enabled=false` translates to `spec.suspend=true` so
    operators can pause without losing the configured time.
    """

    cron: str = DEFAULT_CRON
    timezone: str = DEFAULT_TIMEZONE
    enabled: bool = True
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, v: str) -> str:
        return validate_cron(v)


class CuratorScheduleUpdate(BaseModel):
    """Request body for `PUT /v1/admin/curator/schedule`."""

    cron: str
    timezone: str = DEFAULT_TIMEZONE
    enabled: bool = True
    mode: Literal["weekly", "custom"] = "custom"

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, v: str) -> str:
        return validate_cron(v)
