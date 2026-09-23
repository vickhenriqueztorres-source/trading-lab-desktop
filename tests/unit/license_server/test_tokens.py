"""Unit tests for token family issuance, rotation, and authentication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.license_server.errors import ApiError, ErrorCode
from apps.license_server.tokens import authenticate_access, issue_family, rotate
from tests.unit.license_server.fake_db import FakeConnection, FakeDb


@pytest.fixture
def fake_db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def conn(fake_db: FakeDb) -> FakeConnection:
    return FakeConnection(fake_db)


def test_issue_family(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    tokens = issue_family(conn, cust_id)

    assert tokens.user_id == str(cust_id)
    assert len(tokens.access_token) >= 32
    assert len(tokens.refresh_token) >= 48
    assert tokens.access_expires_at

    # Two tokens created in DB
    assert len(fake_db.api_tokens) == 2
    access_tok = next(t for t in fake_db.api_tokens.values() if t["kind"] == "access")
    refresh_tok = next(t for t in fake_db.api_tokens.values() if t["kind"] == "refresh")

    assert access_tok["customer_id"] == cust_id
    assert refresh_tok["customer_id"] == cust_id
    assert access_tok["family_id"] == refresh_tok["family_id"]
    assert not access_tok["used"]
    assert not access_tok["revoked"]
    assert not refresh_tok["used"]
    assert not refresh_tok["revoked"]


def test_authenticate_access_happy_path(conn: FakeConnection) -> None:
    cust_id = uuid4()
    tokens = issue_family(conn, cust_id)

    # Valid with plain token
    authed_id = authenticate_access(conn, tokens.access_token)
    assert authed_id == cust_id

    # Valid with Bearer prefix
    authed_id = authenticate_access(conn, f"Bearer {tokens.access_token}")
    assert authed_id == cust_id


def test_authenticate_access_invalid_or_missing(conn: FakeConnection) -> None:
    with pytest.raises(ApiError) as exc_info:
        authenticate_access(conn, "")
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID

    with pytest.raises(ApiError) as exc_info:
        authenticate_access(conn, "Bearer bad-token")
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_authenticate_access_expired(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    tokens = issue_family(conn, cust_id)

    # Expire access token
    for tok in fake_db.api_tokens.values():
        if tok["kind"] == "access":
            tok["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(ApiError) as exc_info:
        authenticate_access(conn, tokens.access_token)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_authenticate_access_revoked(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    tokens = issue_family(conn, cust_id)

    # Revoke access token
    for tok in fake_db.api_tokens.values():
        if tok["kind"] == "access":
            tok["revoked"] = True

    with pytest.raises(ApiError) as exc_info:
        authenticate_access(conn, tokens.access_token)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_rotate_happy_path(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    initial_tokens = issue_family(conn, cust_id)

    # Rotate
    rotated_tokens = rotate(conn, initial_tokens.refresh_token)

    assert rotated_tokens.user_id == str(cust_id)
    assert rotated_tokens.access_token != initial_tokens.access_token
    assert rotated_tokens.refresh_token != initial_tokens.refresh_token

    # 4 tokens in db (old access, old refresh [used=True], new access, new refresh)
    assert len(fake_db.api_tokens) == 4

    # New access token is authenticable
    authed_id = authenticate_access(conn, rotated_tokens.access_token)
    assert authed_id == cust_id


def test_rotate_reuse_revokes_entire_family(conn: FakeConnection) -> None:
    cust_id = uuid4()
    initial_tokens = issue_family(conn, cust_id)

    # First rotation succeeds
    rotated_tokens = rotate(conn, initial_tokens.refresh_token)
    assert authenticate_access(conn, rotated_tokens.access_token) == cust_id

    # Replaying the first refresh token triggers AUTH_REFRESH_REUSE
    with pytest.raises(ApiError) as exc_info:
        rotate(conn, initial_tokens.refresh_token)
    assert exc_info.value.code == ErrorCode.AUTH_REFRESH_REUSE

    # Entire family revoked: new access token now rejected
    with pytest.raises(ApiError) as exc_info:
        authenticate_access(conn, rotated_tokens.access_token)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID

    # New refresh token also rejected
    with pytest.raises(ApiError) as exc_info:
        rotate(conn, rotated_tokens.refresh_token)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID
