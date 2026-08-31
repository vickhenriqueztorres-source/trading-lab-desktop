from __future__ import annotations

from apps.core.state.order_machine import can_transition, is_terminal
from packages.domain.orders import OrderState


def test_valid_order_lifecycle_transitions() -> None:
    valid = (
        (OrderState.CREATED, OrderState.ADMITTED),
        (OrderState.ADMITTED, OrderState.RESERVED),
        (OrderState.RESERVED, OrderState.SUBMITTING),
        (OrderState.SUBMITTING, OrderState.ACCEPTED),
        (OrderState.SUBMITTING, OrderState.REJECTED_REMOTE),
        (OrderState.SUBMITTING, OrderState.UNKNOWN),
        (OrderState.UNKNOWN, OrderState.RECONCILING),
        (OrderState.RECONCILING, OrderState.MANUAL_REVIEW),
    )
    assert all(can_transition(before, after) for before, after in valid)


def test_invalid_transitions_and_terminal_states_fail_closed() -> None:
    assert not can_transition(OrderState.CREATED, OrderState.RESERVED)
    assert not can_transition(OrderState.ACCEPTED, OrderState.RECONCILING)
    assert not can_transition(OrderState.UNKNOWN, OrderState.ACCEPTED)
    assert is_terminal(OrderState.ACCEPTED)
    assert is_terminal(OrderState.REJECTED_REMOTE)
    assert is_terminal(OrderState.MANUAL_REVIEW)
    assert not is_terminal(OrderState.UNKNOWN)
