from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable

from packages.domain.models import OrderCommand, OrderStatusQuery, StatusQueryOutcome, WorkerOutcome
from packages.protocol.errors import ProtocolErrorCode
from packages.protocol.messages import OrderStatusResult, WorkerSubmissionResult


class PossibleSendTimeout(TimeoutError):
    pass


class SimulatedWorker:
    """No network access; deterministic outcomes support contract and failure tests."""

    def __init__(
        self,
        outcomes: Iterable[WorkerOutcome] = (WorkerOutcome.ACCEPTED,),
        on_receive: Callable[[OrderCommand], None] | None = None,
    ) -> None:
        self._outcomes = deque(outcomes)
        self._on_receive = on_receive
        self._lock = threading.Lock()
        self.received: list[OrderCommand] = []

    def submit(self, command: OrderCommand) -> WorkerOutcome:
        with self._lock:
            self.received.append(command)
            if self._on_receive is not None:
                self._on_receive(command)
            outcome = self._outcomes.popleft() if self._outcomes else WorkerOutcome.ACCEPTED
        if outcome is WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND:
            raise PossibleSendTimeout("simulated timeout after a possible send")
        return outcome

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        outcome = self.submit(command)
        return WorkerSubmissionResult(
            outcome=outcome,
            broker_order_id=(
                f"SIM-{command.message_id}" if outcome is WorkerOutcome.ACCEPTED else None
            ),
            response_message_id=f"INPROC-{command.message_id}",
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
        )

    def query_order_status(
        self,
        query: OrderStatusQuery,
        *,
        timeout: float | None = None,
    ) -> OrderStatusResult:
        del timeout
        return OrderStatusResult(
            outcome=StatusQueryOutcome.NOT_FOUND,
            evidence=None,
            response_message_id=f"INPROC-STATUS-{query.order_id}",
            correlation_id=query.correlation_id,
            causation_id=f"INPROC-QUERY-{query.order_id}",
            reason_code=ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value,
        )
