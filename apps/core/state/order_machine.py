"""Fail-closed order state transitions for the enterprise foundation."""

from __future__ import annotations

from packages.domain.orders import OrderState

VALID_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.ADMITTED}),
    OrderState.ADMITTED: frozenset({OrderState.RESERVED}),
    OrderState.RESERVED: frozenset({OrderState.SUBMITTING}),
    OrderState.SUBMITTING: frozenset(
        {OrderState.ACCEPTED, OrderState.REJECTED_REMOTE, OrderState.UNKNOWN}
    ),
    OrderState.ACCEPTED: frozenset(),
    OrderState.REJECTED_REMOTE: frozenset(),
    OrderState.UNKNOWN: frozenset({OrderState.RECONCILING}),
    OrderState.RECONCILING: frozenset(
        {OrderState.ACCEPTED, OrderState.REJECTED_REMOTE, OrderState.MANUAL_REVIEW}
    ),
    OrderState.MANUAL_REVIEW: frozenset(),
}


def can_transition(from_state: OrderState, to_state: OrderState) -> bool:
    try:
        source = OrderState(from_state)
        target = OrderState(to_state)
    except ValueError:
        return False
    return target in VALID_TRANSITIONS[source]


def is_terminal(state: OrderState) -> bool:
    try:
        normalized = OrderState(state)
    except ValueError:
        return False
    return not VALID_TRANSITIONS[normalized]


__all__ = ["VALID_TRANSITIONS", "can_transition", "is_terminal"]
