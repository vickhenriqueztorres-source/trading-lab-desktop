from __future__ import annotations


def monotonic_gap_exceeds(
    now: float,
    previous: float,
    *,
    max_gap_seconds: float,
) -> bool:
    """Return whether a monotonic observation gap proves process suspension."""

    if max_gap_seconds <= 0:
        raise ValueError("suspension gap threshold must be positive")
    return now - previous > max_gap_seconds
