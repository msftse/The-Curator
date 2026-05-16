from __future__ import annotations

import pytest

from backend.core.errors import (
    AlreadyPublished,
    BundleTooLarge,
    DomainError,
    Forbidden,
    InvalidBundle,
    LockUnavailable,
    NotImplementedM0,
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
    ],
)
def test_error_codes_and_status(exc: DomainError, code: str, status: int):
    assert exc.error_code == code
    assert exc.http_status == status
    assert isinstance(exc, DomainError)
