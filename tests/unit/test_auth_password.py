"""argon2id password hashing and verification (offline, no database)."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from services.backend.app.api.auth import (
    PASSWORD_MAX_LEN,
    PASSWORD_MIN_LEN,
    hash_password,
    verify_password,
)


def test_hash_is_argon2id_and_verifies() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "correct horse battery staple") is True


def test_wrong_password_fails() -> None:
    digest = hash_password("correct horse battery staple")
    assert verify_password(digest, "wrong password") is False


def test_hash_is_not_reversible() -> None:
    password = "correct horse battery staple"
    digest = hash_password(password)
    assert digest != password
    assert password not in digest
    # The raw KDF rejects a wrong secret instead of revealing the stored one.
    with pytest.raises(VerifyMismatchError):
        PasswordHasher().verify(digest, "another secret entirely")


def test_two_hashes_of_same_password_differ_by_salt() -> None:
    first = hash_password("same password")
    second = hash_password("same password")
    assert first != second
    assert verify_password(first, "same password") is True
    assert verify_password(second, "same password") is True


def test_verify_with_malformed_hash_returns_false_not_raises() -> None:
    assert verify_password("not-a-hash", "whatever") is False
    assert verify_password("", "whatever") is False


def test_password_length_policy_boundaries() -> None:
    assert (PASSWORD_MIN_LEN, PASSWORD_MAX_LEN) == (8, 128)
    shortest = "x" * PASSWORD_MIN_LEN
    longest = "x" * PASSWORD_MAX_LEN
    assert verify_password(hash_password(shortest), shortest) is True
    assert verify_password(hash_password(longest), longest) is True