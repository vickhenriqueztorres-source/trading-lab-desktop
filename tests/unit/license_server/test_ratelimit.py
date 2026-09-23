"""Unit tests for InMemoryRateLimiter."""

from __future__ import annotations

from apps.license_server.ratelimit import InMemoryRateLimiter


def test_rate_limiter_allows_under_limit_and_blocks_over_limit() -> None:
    current_time = 100.0

    def clock() -> float:
        return current_time

    limiter = InMemoryRateLimiter(clock=clock)
    key = "user@example.invalid"

    # 3 requests allowed in 600s
    allowed, retry_after = limiter.check(key, max_requests=3, window_seconds=600.0)
    assert allowed is True
    assert retry_after == 0

    allowed, retry_after = limiter.check(key, max_requests=3, window_seconds=600.0)
    assert allowed is True
    assert retry_after == 0

    allowed, retry_after = limiter.check(key, max_requests=3, window_seconds=600.0)
    assert allowed is True
    assert retry_after == 0

    # 4th request must be rate limited
    allowed, retry_after = limiter.check(key, max_requests=3, window_seconds=600.0)
    assert allowed is False
    assert retry_after > 0

    # Other key should not be blocked
    other = "other@example.invalid"
    allowed, retry_after = limiter.check(other, max_requests=3, window_seconds=600.0)
    assert allowed is True
    assert retry_after == 0

    # Advance clock past window
    current_time += 601.0
    allowed, retry_after = limiter.check(key, max_requests=3, window_seconds=600.0)
    assert allowed is True
    assert retry_after == 0
