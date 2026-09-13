"""Rastreador local de idempotência e correlação para operações de broker.

Garante que ordens mutantes (buy, place_order) mantenham rastreabilidade de chave única,
impeçam disparos duplicados e sejam marcadas como UNKNOWN diante de qualquer timeout,
bloqueando novas tentativas até que a reconciliação comprove o estado real.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from apps.core.broker_resilience.models import MUTATING_OPERATIONS, BrokerName


class IdempotencyState(StrEnum):
    """Estados do ciclo de vida de uma operação idempotente."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    RECONCILED = "RECONCILED"


def create_idempotency_key(
    broker: BrokerName | str,
    operation: str,
    trade_id: str | None = None,
) -> str:
    """Gera chave estável e determinística para idempotência de chamada."""
    b_name = BrokerName.from_value(broker).value
    op_clean = operation.strip().lower()
    t_id = (trade_id or "").strip()
    if not t_id:
        t_id = str(uuid4())
    raw = f"{b_name}:{op_clean}:{t_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"idem-{b_name.lower()}-{op_clean}-{digest}"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Registro imutável de uma intenção ou despacho registrado no tracker."""

    key: str
    broker: BrokerName
    operation: str
    trade_id: str | None
    state: IdempotencyState
    created_at: float
    updated_at: float
    attempts: int
    details: dict[str, Any] = field(default_factory=dict)


class IdempotencyTracker:
    """Tracker thread-safe de idempotência em memória para a camada de resiliência."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._records: dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()

    def can_dispatch(self, key: str) -> tuple[bool, str | None]:
        """Avalia se a chave pode ser despachada ou se está travada por duplicidade/ambiguidade."""
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return True, None

            if record.state is IdempotencyState.CONFIRMED:
                return False, "ALREADY_CONFIRMED"

            if record.state is IdempotencyState.UNKNOWN:
                return False, "ORDER_UNKNOWN_PENDING_RECONCILIATION"

            if record.state is IdempotencyState.PENDING and record.operation in MUTATING_OPERATIONS:
                return False, "MUTATING_OPERATION_IN_FLIGHT"

            return True, None

    def start_attempt(
        self,
        key: str,
        broker: BrokerName | str,
        operation: str,
        trade_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> IdempotencyRecord:
        """Registra o início de uma tentativa para a chave informada."""
        b_name = BrokerName.from_value(broker)
        op_clean = operation.strip().lower()
        now = self._clock()

        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.state is IdempotencyState.UNKNOWN:
                    raise RuntimeError(
                        f"Chave de idempotência {key} está em estado UNKNOWN. "
                        "Reconciliação é obrigatória antes de qualquer nova tentativa."
                    )
                if existing.state is IdempotencyState.CONFIRMED:
                    return existing

                record = IdempotencyRecord(
                    key=key,
                    broker=b_name,
                    operation=op_clean,
                    trade_id=trade_id or existing.trade_id,
                    state=IdempotencyState.PENDING,
                    created_at=existing.created_at,
                    updated_at=now,
                    attempts=existing.attempts + 1,
                    details={**existing.details, **(details or {})},
                )
            else:
                record = IdempotencyRecord(
                    key=key,
                    broker=b_name,
                    operation=op_clean,
                    trade_id=trade_id,
                    state=IdempotencyState.PENDING,
                    created_at=now,
                    updated_at=now,
                    attempts=1,
                    details=details or {},
                )

            self._records[key] = record
            return record

    def mark_confirmed(self, key: str, details: dict[str, Any] | None = None) -> None:
        """Transita a chave para CONFIRMED."""
        now = self._clock()
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                self._records[key] = IdempotencyRecord(
                    key=key,
                    broker=record.broker,
                    operation=record.operation,
                    trade_id=record.trade_id,
                    state=IdempotencyState.CONFIRMED,
                    created_at=record.created_at,
                    updated_at=now,
                    attempts=record.attempts,
                    details={**record.details, **(details or {})},
                )

    def mark_unknown(self, key: str, reason: str | None = None) -> None:
        """Transita a chave para UNKNOWN (bloqueia tentativas subsequentes)."""
        now = self._clock()
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                self._records[key] = IdempotencyRecord(
                    key=key,
                    broker=record.broker,
                    operation=record.operation,
                    trade_id=record.trade_id,
                    state=IdempotencyState.UNKNOWN,
                    created_at=record.created_at,
                    updated_at=now,
                    attempts=record.attempts,
                    details={**record.details, "unknown_reason": reason or "TIMEOUT_OR_DISCONNECT"},
                )

    def mark_reconciled(self, key: str, final_outcome: str | None = None) -> None:
        """Transita a chave para RECONCILED após confirmação conclusiva."""
        now = self._clock()
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                self._records[key] = IdempotencyRecord(
                    key=key,
                    broker=record.broker,
                    operation=record.operation,
                    trade_id=record.trade_id,
                    state=IdempotencyState.RECONCILED,
                    created_at=record.created_at,
                    updated_at=now,
                    attempts=record.attempts,
                    details={**record.details, "reconciled_outcome": final_outcome or "RESOLVED"},
                )

    def get_record(self, key: str) -> IdempotencyRecord | None:
        """Consulta o registro associado a uma chave."""
        with self._lock:
            return self._records.get(key)
