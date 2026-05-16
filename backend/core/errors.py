"""Stable error codes + FastAPI exception handlers.

Domain errors carry a stable `error_code` so the frontend can branch on
identity, not on message strings.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class DomainError(Exception):
    """Base class for all expected business-logic errors."""

    error_code: str = "INTERNAL_ERROR"
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, *, metadata: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.metadata = metadata or {}


class SkillNotFound(DomainError):
    error_code = "SKILL_NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND


class InvalidBundle(DomainError):
    error_code = "INVALID_BUNDLE"
    http_status = status.HTTP_400_BAD_REQUEST


class BundleTooLarge(DomainError):
    error_code = "BUNDLE_TOO_LARGE"
    http_status = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


class AlreadyPublished(DomainError):
    error_code = "ALREADY_PUBLISHED"
    http_status = status.HTTP_409_CONFLICT


class SkillPinned(DomainError):
    """Admin attempted a curator-equivalent op (e.g. manual archive) on a pinned skill.

    Pinning is absolute per AGENTS.md §5: pinned skills are immune to every
    auto-transition AND to admin-issued archival. Operator must unpin first.
    """

    error_code = "SKILL_PINNED"
    http_status = status.HTTP_409_CONFLICT


class InvalidStatusTransition(DomainError):
    """Caller asked for a state transition the current status does not allow.

    Used by admin archive when status != 'approved' (and analogous flows).
    """

    error_code = "INVALID_STATUS_TRANSITION"
    http_status = status.HTTP_409_CONFLICT


class LockUnavailable(DomainError):
    error_code = "LOCK_UNAVAILABLE"
    http_status = status.HTTP_423_LOCKED


class Forbidden(DomainError):
    error_code = "FORBIDDEN"
    http_status = status.HTTP_403_FORBIDDEN


class Unauthorized(DomainError):
    error_code = "UNAUTHORIZED"
    http_status = status.HTTP_401_UNAUTHORIZED


class NotImplementedM0(DomainError):
    error_code = "NOT_IMPLEMENTED_M0"
    http_status = status.HTTP_501_NOT_IMPLEMENTED


class InvalidToken(DomainError):
    error_code = "INVALID_TOKEN"
    http_status = status.HTTP_401_UNAUTHORIZED


class RevokedApiKey(DomainError):
    error_code = "REVOKED_API_KEY"
    http_status = status.HTTP_401_UNAUTHORIZED


class MissingScope(DomainError):
    error_code = "MISSING_SCOPE"
    http_status = status.HTTP_403_FORBIDDEN


# ---- M2 — Curator ----


class CuratorPaused(DomainError):
    error_code = "CURATOR_PAUSED"
    http_status = status.HTTP_409_CONFLICT


class SnapshotNotFound(DomainError):
    error_code = "SNAPSHOT_NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND


class RestoreFailed(DomainError):
    error_code = "RESTORE_FAILED"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


class CuratorRunReportNotFound(DomainError):
    error_code = "CURATOR_RUN_REPORT_NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND


# ---- M3 — Curator LLM review ----


class ReviewProposalNotFound(DomainError):
    error_code = "REVIEW_PROPOSAL_NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND


class ReviewProposalStale(DomainError):
    error_code = "REVIEW_PROPOSAL_STALE"
    http_status = status.HTTP_409_CONFLICT


class ReviewProposalNotPending(DomainError):
    error_code = "REVIEW_PROPOSAL_NOT_PENDING"
    http_status = status.HTTP_409_CONFLICT


class LLMProviderError(DomainError):
    error_code = "LLM_PROVIDER_ERROR"
    http_status = status.HTTP_502_BAD_GATEWAY


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_handler(_: Request, exc: DomainError) -> JSONResponse:
        body = {"error_code": exc.error_code, "message": exc.message}
        if exc.metadata:
            body["metadata"] = exc.metadata
        return JSONResponse(status_code=exc.http_status, content=body)
