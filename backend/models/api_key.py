"""Cosmos doc + API DTOs for API keys (M1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Scope = Literal["catalog:read", "usage:write"]


class ApiKeyDoc(BaseModel):
    """Persisted shape — hash only, NEVER the raw key."""

    id: str  # Cosmos id; equal to key_id.
    key_id: str
    name: str
    scopes: list[Scope]
    hash_sha256: str
    created_by: str
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class ApiKeyIssueRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[Scope] = Field(default_factory=lambda: ["catalog:read"])


class ApiKeyIssueResponse(BaseModel):
    """The ONE time the raw key is ever returned to the caller."""

    key_id: str
    name: str
    scopes: list[Scope]
    raw_key: str
    created_at: datetime


class ApiKeyListItem(BaseModel):
    key_id: str
    name: str
    scopes: list[Scope]
    created_by: str
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
