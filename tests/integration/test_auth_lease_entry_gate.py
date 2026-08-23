from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from apps.auth_agent import (
    AuthAgent,
    CoreLeaseEntryAuthorizer,
    EntryAuthorizationError,
    FakeIdentityService,
)
from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.runtime import CoreRuntime
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import (
    BrokerOrderEvent,
    ExternalOrderStatus,
    OrderRequest,
    WorkerOutcome,
)
from packages.identity import OtpCode
from packages.licensing import AuthorizationReason, LeaseVerifier
from packages.persistence import SingleDatabaseWriter, StateReader
from packages.security import SimulatedUserScopedVault


@dataclass
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def authorized_components(
    clock: MutableClock,
) -> tuple[AuthAgent, FakeIdentityService, HealthGate, CoreLeaseEntryAuthorizer]:
    service = FakeIdentityService(now=clock.now, lease_ttl=timedelta(minutes=30))
    agent = AuthAgent(
        service,
        SimulatedUserScopedVault("integration-windows-user"),
        LeaseVerifier(service.lease_verification_keys),
        now=clock.now,
    )
    challenge = agent.start_login("integration-user@example.invalid")
    code: OtpCode = service.take_otp_for_testing(challenge.challenge_id)
    assert agent.complete_login(code).new_entries_allowed is True
    health = HealthGate()
    return agent, service, health, CoreLeaseEntryAuthorizer(agent, health)


def settlement_event(
    request: OrderRequest,
    order_id: str,
    broker_order_id: str,
) -> BrokerOrderEvent:
    now = datetime.now(UTC)
    canonical: dict[str, object] = {
        "event_id": str(uuid4()),
        "event_version": 1,
        "broker": request.broker.value,
        "account_id": request.account_id,
        "client_order_ref": order_id,
        "broker_order_id": broker_order_id,
        "correlation_id": request.correlation_id,
        "external_sequence": 1,
        "external_status": ExternalOrderStatus.SETTLED.value,
        "occurred_at": now.isoformat(),
        "observed_at": now.isoformat(),
        "product": request.product,
        "symbol": request.symbol,
        "direction": request.direction.value,
        "amount_minor": request.amount.minor_units,
        "currency": request.amount.currency,
        "result_minor": 125,
        "result_currency": request.amount.currency,
    }
    return BrokerOrderEvent.from_payload(
        {**canonical, "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical)}
    )


@pytest.mark.parametrize("block_kind", ["expiry", "revocation"])
def test_expiry_or_revocation_blocks_only_new_entries_and_settles_open_order(
    tmp_path: Path,
    order_request: OrderRequest,
    block_kind: str,
) -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    agent, service, health, authorizer = authorized_components(clock)
    writer = SingleDatabaseWriter(tmp_path / f"{block_kind}.db")
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    coordinator = OrderCoordinator(
        writer,
        worker,
        health,
        entry_authorizer=authorizer,
    )
    reader = StateReader(writer.path)
    try:
        persisted = coordinator.submit(order_request)
        assert worker.received[0].correlation_id == order_request.correlation_id
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "ACCEPTED"

        if block_kind == "expiry":
            clock.advance(timedelta(minutes=30))
        else:
            claims = LeaseVerifier(service.lease_verification_keys).verify(agent.current_lease)
            service.revoke_device(claims.device_id)
            assert agent.renew_silently().reason is AuthorizationReason.DEVICE_REVOKED
        blocked = authorizer.refresh_health(order_request.broker, order_request.strategy_id)
        assert blocked.new_entries_allowed is False
        assert blocked.open_order_follow_up_allowed is True
        assert blocked.reconciliation_allowed is True

        before = reader.count("trade_intents")
        next_request = replace(
            order_request,
            correlation_id=f"blocked-{block_kind}",
            account_id="demo-account-2",
        )
        with pytest.raises(EntryAuthorizationError) as blocked_entry:
            coordinator.submit(next_request)
        assert blocked_entry.value.reason_code in {
            AuthorizationReason.LEASE_EXPIRED.value,
            AuthorizationReason.DEVICE_REVOKED.value,
        }
        assert reader.count("trade_intents") == before
        assert len(worker.received) == 1

        broker_order_id = f"SIM-{persisted.message_id}"
        result = writer.apply_normalized_broker_event(
            settlement_event(order_request, persisted.order_id, broker_order_id)
        )
        assert result.order_state.value == "SETTLED"
        assert reader.reservation_for_intent(persisted.intent_id)["state"] == "RELEASED"
        assert reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        writer.close()


def test_core_runtime_wires_only_the_reduced_authorization_boundary(
    tmp_path: Path,
    order_request: OrderRequest,
) -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    agent, _, _, _ = authorized_components(clock)
    runtime = CoreRuntime(
        tmp_path / "licensed-runtime",
        entry_authorizer_factory=lambda gate: CoreLeaseEntryAuthorizer(agent, gate),
    )
    runtime.start()
    try:
        persisted = runtime.submit(order_request, dispatch=False)
        assert runtime.reader.one("orders", "order_id", persisted.order_id) is not None
        clock.advance(timedelta(minutes=30))
        with pytest.raises(EntryAuthorizationError) as expired:
            runtime.submit(
                replace(
                    order_request,
                    correlation_id="runtime-expired",
                    account_id="demo-account-2",
                ),
                dispatch=False,
            )
        assert expired.value.reason_code == AuthorizationReason.LEASE_EXPIRED.value
        assert runtime.reader.count("trade_intents") == 1
    finally:
        runtime.shutdown()
