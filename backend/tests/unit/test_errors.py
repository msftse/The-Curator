from __future__ import annotations

import pytest

from backend.core.errors import (
    AlreadyPublished,
    BundleTooLarge,
    DomainError,
    Forbidden,
    InvalidBundle,
    InvalidToken,
    LLMProviderError,
    LockUnavailable,
    MissingScope,
    NotImplementedM0,
    ReviewProposalNotFound,
    ReviewProposalNotPending,
    ReviewProposalStale,
    RevokedApiKey,
    SkillNotFound,
    Unauthorized,
)


@pytest.mark.parametrize(
    "exc,code,status",
    [
        (SkillNotFound("x"), "SKILL_NOT_FOUND", 404),
        (InvalidBundle("x"), "INVALID_BUNDLE", 400),
        (BundleTooLarge("x"), "BUNDLE_TOO_LARGE", 413),
        (AlreadyPublished("x"), "ALREADY_PUBLISHED", 409),
        (LockUnavailable("x"), "LOCK_UNAVAILABLE", 423),
        (Forbidden("x"), "FORBIDDEN", 403),
        (Unauthorized("x"), "UNAUTHORIZED", 401),
        (NotImplementedM0("x"), "NOT_IMPLEMENTED_M0", 501),
        (InvalidToken("x"), "INVALID_TOKEN", 401),
        (RevokedApiKey("x"), "REVOKED_API_KEY", 401),
        (MissingScope("x"), "MISSING_SCOPE", 403),
        (ReviewProposalNotFound("x"), "REVIEW_PROPOSAL_NOT_FOUND", 404),
        (ReviewProposalStale("x"), "REVIEW_PROPOSAL_STALE", 409),
        (ReviewProposalNotPending("x"), "REVIEW_PROPOSAL_NOT_PENDING", 409),
        (LLMProviderError("x"), "LLM_PROVIDER_ERROR", 502),
    ],
)
def test_error_codes_and_status(exc: DomainError, code: str, status: int):
    assert exc.error_code == code
    assert exc.http_status == status
    assert isinstance(exc, DomainError)
