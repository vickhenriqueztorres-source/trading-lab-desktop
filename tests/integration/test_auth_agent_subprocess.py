from __future__ import annotations

import base64
import secrets
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from apps.auth_agent import CoreLeaseEntryAuthorizer, EntryAuthorizationError
from apps.core.auth_supervisor import AuthAgentHealthState, AuthAgentSupervisor
from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import (
    BrokerOrderEvent,
    ExternalOrderStatus,
    OrderRequest,
    WorkerOutcome,
)
from packages.licensing import AuthorizationReason, SignedLease
from packages.persistence import SingleDatabaseWriter, StateReader
from packages.security import SecretValue, WindowsUserScopedVault

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="subprocess persistence uses the Windows DPAPI vault",
)


def _settlement_event(
    request: OrderRequest,
    order_id: str,
    broker_order_id: str,
) -> BrokerOrderEvent:
    now = datetime.now(UTC)
    canonical: dict[str, object] = {
        "account_id": request.account_id,
        "amount_minor": request.amount.minor_units,
        "broker": request.broker.value,
        "broker_order_id": broker_order_id,
        "client_order_ref": order_id,
        "correlation_id": request.correlation_id,
        "currency": request.amount.currency,
        "direction": request.direction.value,
        "event_id": str(uuid4()),
        "event_version": 1,
        "external_sequence": 1,
        "external_status": ExternalOrderStatus.SETTLED.value,
        "observed_at": now.isoformat(),
        "occurred_at": now.isoformat(),
        "product": request.product,
        "result_currency": request.amount.currency,
        "result_minor": 125,
        "symbol": request.symbol,
    }
    return BrokerOrderEvent.from_payload(
        {**canonical, "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical)}
    )


def _authorize(supervisor: AuthAgentSupervisor) -> str:
    challenge = supervisor.start_login("subprocess-user@example.invalid")
    otp = supervisor.take_test_otp()
    result = supervisor.submit_otp(challenge.challenge_id, otp)
    assert result.user_id_preview is not None
    return result.user_id_preview


def test_auth_agent_subprocess_login_kill_restart_and_open_order_isolation(
    tmp_path: Path,
    order_request: OrderRequest,
) -> None:
    profile = tmp_path / "auth-profile"
    supervisor = AuthAgentSupervisor(profile, enable_test_otp=True)
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(writer.path)
    health = HealthGate()
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    authorizer = CoreLeaseEntryAuthorizer(supervisor, health)
    coordinator = OrderCoordinator(writer, worker, health, entry_authorizer=authorizer)
    try:
        supervisor.start()
        started_process = supervisor.process
        assert started_process is not None
        assert started_process.args == [sys.executable, "-m", "apps.auth_agent.runner"]
        preview = _authorize(supervisor)
        original_status = supervisor.status()
        assert original_status.user_id_preview == preview
        assert original_status.device_id is not None
        assert supervisor.authorization("DERIV", "strategy-test").new_entries_allowed

        persisted = coordinator.submit(order_request)
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "ACCEPTED"
        assert reader.count("trade_intents") == 1

        process = supervisor.process
        assert process is not None
        process.kill()
        process.wait(timeout=2.0)
        assert supervisor.wait_for_state(AuthAgentHealthState.UNAVAILABLE)

        blocked_request = replace(
            order_request,
            account_id="demo-account-auth-down",
            correlation_id=f"auth-down-{secrets.token_hex(8)}",
        )
        with pytest.raises(EntryAuthorizationError) as blocked:
            coordinator.submit(blocked_request)
        assert blocked.value.reason_code == AuthorizationReason.AUTH_AGENT_UNAVAILABLE.value
        assert reader.count("trade_intents") == 1
        assert len(worker.received) == 1

        broker_order_id = f"SIM-{persisted.message_id}"
        settlement = writer.apply_normalized_broker_event(
            _settlement_event(order_request, persisted.order_id, broker_order_id)
        )
        assert settlement.order_state.value == "SETTLED"
        assert reader.reservation_for_intent(persisted.intent_id)["state"] == "RELEASED"

        supervisor.restart()
        restored_status = supervisor.status()
        assert restored_status.user_id_preview == preview
        assert restored_status.device_id == original_status.device_id
        assert supervisor.authorization("DERIV", "strategy-test").new_entries_allowed
    finally:
        supervisor.shutdown()
        writer.close()


def test_auth_agent_subprocess_expired_and_corrupted_lease_fail_closed(tmp_path: Path) -> None:
    expiry_supervisor = AuthAgentSupervisor(
        tmp_path / "expiry-profile",
        enable_test_otp=True,
        test_lease_ttl_seconds=0.25,
    )
    try:
        expiry_supervisor.start()
        _authorize(expiry_supervisor)
        time.sleep(0.35)
        expired = expiry_supervisor.authorization("DERIV", "strategy-test")
        assert expired.new_entries_allowed is False
        assert expired.reason is AuthorizationReason.LEASE_EXPIRED
        assert expired.open_order_follow_up_allowed is True
        assert expired.reconciliation_allowed is True
    finally:
        expiry_supervisor.shutdown()

    profile = tmp_path / "corrupted-profile"
    corruption_supervisor = AuthAgentSupervisor(profile, enable_test_otp=True)
    try:
        corruption_supervisor.start()
        _authorize(corruption_supervisor)
    finally:
        corruption_supervisor.shutdown()

    vault = WindowsUserScopedVault(profile / "vault")
    stored_lease = vault.load("licensing.signed_lease")
    assert stored_lease is not None
    signed = SignedLease.from_bytes(stored_lease.reveal_bytes())
    payload = bytearray(base64.urlsafe_b64decode(signed.payload_b64))
    payload[-1] ^= 0x01
    tampered = SignedLease(
        key_id=signed.key_id,
        payload_b64=base64.urlsafe_b64encode(payload).decode("ascii"),
        signature_b64=signed.signature_b64,
    )
    vault.store("licensing.signed_lease", SecretValue(tampered.to_bytes()))

    try:
        corruption_supervisor.start()
        blocked = corruption_supervisor.authorization("DERIV", "strategy-test")
        assert blocked.new_entries_allowed is False
        assert blocked.reason is AuthorizationReason.LEASE_INVALID
        assert blocked.open_order_follow_up_allowed is True
        assert blocked.reconciliation_allowed is True
    finally:
        corruption_supervisor.shutdown()
