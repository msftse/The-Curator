"""Structured JSON logger with context-var skill_id/actor injection.

Use `bind(skill_id=..., actor=...)` at the top of any request handler that
mutates a skill, so every log line for that request includes the context.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

_skill_id_var: ContextVar[str | None] = ContextVar("skill_id", default=None)
_actor_var: ContextVar[str | None] = ContextVar("actor", default=None)


def bind(*, skill_id: str | None = None, actor: str | None = None) -> None:
    """Bind context for the current async task."""
    if skill_id is not None:
        _skill_id_var.set(skill_id)
    if actor is not None:
        _actor_var.set(actor)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        skill_id = _skill_id_var.get()
        actor = _actor_var.get()
        if skill_id:
            payload["skill_id"] = skill_id
        if actor:
            payload["actor"] = actor
        # Capture extras
        for key, val in record.__dict__.items():
            if key in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
            }:
                continue
            payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Idempotently configure the root logger for JSON output to stdout."""
    root = logging.getLogger()
    if getattr(root, "_skillhub_configured", False):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level)
    root._skillhub_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
