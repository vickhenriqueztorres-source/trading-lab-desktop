from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from apps.auth_agent import (
    AuthAgent,
    AuthAgentState,
    FakeIdentityService,
    FakeIdentityServiceError,
    FakeIdentityServiceErrorCode,
)
from packages.identity import OtpCode, PkceMaterial, SessionTokens
from packages.licensing import AuthorizationReason, LeaseVerifier, SignedLease
from packages.observability import InMemoryEventSink
from packages.security import SimulatedUserScopedVault


@dataclass
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def build_authorized_agent(
    clock: MutableClock,
    *,
    lease_ttl: timedelta = timedelta(days=7),
    backing: dict[str, bytes] | None = None,
    client_version: str = "0.0.1",
) -> tuple[AuthAgent, FakeIdentityService, SimulatedUserScopedVault, dict[str, bytes]]:
    shared = backing if backing is not None else {}
    vault = SimulatedUserScopedVault("windows-user-a", shared)
    service = FakeIdentityService(now=clock.now, lease_ttl=lease_ttl)
    agent = AuthAgent(
        service,
        vault,
        LeaseVerifier(service.lease_verification_keys),
        now=clock.now,
        client_version=client_version,
    )
    challenge = agent.start_login("phase0-user@example.invalid")
    code = service.take_otp_for_testing(challenge.challenge_id)
    decision = agent.complete_login(code)
    assert decision.new_entries_allowed is (client_version == "0.0.1")
    return agent, service, vault, shared


def raw_login(
    service: FakeIdentityService,
) -> tuple[dict[str, object], str, str, OtpCode]:
    pkce = PkceMaterial.create()
    response = service.start_login("rotation-user@example.invalid", pkce.challenge)
    challenge_id = response["challenge_id"]
    assert isinstance(challenge_id, str)
    code = service.take_otp_for_testing(challenge_id)
    tokens = service.complete_login(challenge_id, code, pkce.verifier.reveal_text())
    return tokens, challenge_id, pkce.verifier.reveal_text(), code


def test_otp_pkce_login_uses_generated_code_and_redacts_sensitive_values() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    events = InMemoryEventSink()
    service = FakeIdentityService(now=clock.now)
    agent = AuthAgent(
        service,
        SimulatedUserScopedVault("windows-user-a"),
        LeaseVerifier(service.lease_verification_keys),
        now=clock.now,
        event_sink=events,
    )
    challenge = agent.start_login("phase0-user@example.invalid")
    code = service.take_otp_for_testing(challenge.challenge_id)
    decision = agent.complete_login(code)

    assert decision.new_entries_allowed is True
    assert agent.state is AuthAgentState.AUTHORIZED
    assert repr(code) == "OtpCode(<redacted>)"
    assert repr(agent.current_lease).endswith("<redacted>)")
    serialized_events = repr(events.events)
    assert code.value not in serialized_events
    assert "phase0-user@example.invalid" not in serialized_events
    assert "access_token" not in serialized_events
    assert "refresh_token" not in serialized_events


def test_otp_expiry_duplicate_completion_and_invalid_pkce_fail_closed() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    service = FakeIdentityService(now=clock.now)
    pkce = PkceMaterial.create()
    response = service.start_login("phase0-user@example.invalid", pkce.challenge)
    challenge_id = response["challenge_id"]
    assert isinstance(challenge_id, str)
    code = service.take_otp_for_testing(challenge_id)

    with pytest.raises(FakeIdentityServiceError) as wrong_pkce:
        service.complete_login(challenge_id, code, secrets.token_urlsafe(64))
    assert wrong_pkce.value.code is FakeIdentityServiceErrorCode.PKCE_INVALID

    service.complete_login(challenge_id, code, pkce.verifier.reveal_text())
    with pytest.raises(FakeIdentityServiceError) as duplicate:
        service.complete_login(challenge_id, code, pkce.verifier.reveal_text())
    assert duplicate.value.code is FakeIdentityServiceErrorCode.CHALLENGE_INVALID

    second = service.start_login("phase0-user@example.invalid", pkce.challenge)
    second_id = second["challenge_id"]
    assert isinstance(second_id, str)
    second_code = service.take_otp_for_testing(second_id)
    clock.advance(timedelta(minutes=6))
    with pytest.raises(FakeIdentityServiceError) as expired:
        service.complete_login(second_id, second_code, pkce.verifier.reveal_text())
    assert expired.value.code is FakeIdentityServiceErrorCode.CHALLENGE_EXPIRED


def test_refresh_rotation_reuse_revokes_the_token_family() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    service = FakeIdentityService(now=clock.now)
    tokens, _, _, _ = raw_login(service)
    first_refresh = tokens["refresh_token"]
    assert isinstance(first_refresh, str)
    rotated = service.refresh_session(first_refresh)
    second_refresh = rotated["refresh_token"]
    assert isinstance(second_refresh, str) and second_refresh != first_refresh

    with pytest.raises(FakeIdentityServiceError) as reused:
        service.refresh_session(first_refresh)
    assert reused.value.code is FakeIdentityServiceErrorCode.TOKEN_REUSE
    with pytest.raises(FakeIdentityServiceError) as family_revoked:
        service.refresh_session(second_refresh)
    assert family_revoked.value.code is FakeIdentityServiceErrorCode.TOKEN_INVALID


def test_device_and_valid_signed_lease_restore_after_agent_restart() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    agent, service, _, backing = build_authorized_agent(clock)
    original_claims = LeaseVerifier(service.lease_verification_keys).verify(agent.current_lease)

    restarted = AuthAgent(
        service,
        SimulatedUserScopedVault("windows-user-a", backing),
        LeaseVerifier(service.lease_verification_keys),
        now=clock.now,
    )
    restored = restarted.restore()
    restored_claims = LeaseVerifier(service.lease_verification_keys).verify(restarted.current_lease)
    assert restored.new_entries_allowed is True
    assert restarted.state is AuthAgentState.OFFLINE_AUTHORIZED
    assert restored_claims.device_id == original_claims.device_id

    other_user = AuthAgent(
        service,
        SimulatedUserScopedVault("windows-user-b", backing),
        LeaseVerifier(service.lease_verification_keys),
        now=clock.now,
    )
    assert other_user.restore().reason is AuthorizationReason.AUTH_REQUIRED


def test_lease_tampering_and_external_session_schema_are_rejected() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    agent, service, _, _ = build_authorized_agent(clock)
    signed = agent.current_lease
    assert signed is not None
    payload = bytearray(base64.urlsafe_b64decode(signed.payload_b64))
    payload[-2] ^= 1
    tampered = SignedLease(
        signed.key_id,
        base64.urlsafe_b64encode(payload).decode("ascii"),
        signed.signature_b64,
    )
    decision = LeaseVerifier(service.lease_verification_keys).evaluate(
        tampered,
        now=clock.now(),
        expected_user_id="any",
        expected_device_id="any",
        client_version="0.0.1",
        broker="DERIV",
        strategy_pack="strategy-test",
    )
    assert decision.reason is AuthorizationReason.LEASE_INVALID_SIGNATURE
    assert decision.open_order_follow_up_allowed is True
    assert decision.reconciliation_allowed is True

    with pytest.raises(ValueError):
        SessionTokens.from_external_payload({"access_token": "unexpected-partial-response"})


def test_offline_valid_lease_survives_service_outage_then_expiry_blocks_entries() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    agent, service, _, _ = build_authorized_agent(clock, lease_ttl=timedelta(hours=1))
    service.set_available(False)
    offline = agent.renew_silently()
    assert offline.new_entries_allowed is True
    assert agent.state is AuthAgentState.OFFLINE_AUTHORIZED

    clock.advance(timedelta(hours=1))
    expired = agent.renew_silently()
    assert expired.reason is AuthorizationReason.LEASE_EXPIRED
    assert expired.new_entries_allowed is False
    assert expired.open_order_follow_up_allowed is True
    assert expired.reconciliation_allowed is True


def test_known_device_revocation_and_client_or_entitlement_mismatch_block() -> None:
    clock = MutableClock(datetime(2026, 8, 20, tzinfo=UTC))
    agent, service, _, _ = build_authorized_agent(clock)
    claims = LeaseVerifier(service.lease_verification_keys).verify(agent.current_lease)
    service.revoke_device(claims.device_id)
    revoked = agent.renew_silently()
    assert revoked.reason is AuthorizationReason.DEVICE_REVOKED
    assert revoked.new_entries_allowed is False
    assert revoked.open_order_follow_up_allowed is True

    incompatible, _, _, _ = build_authorized_agent(clock, client_version="9.0.0")
    assert incompatible.authorization("DERIV", "strategy-test").reason is (
        AuthorizationReason.CLIENT_INCOMPATIBLE
    )
    entitled, _, _, _ = build_authorized_agent(clock)
    assert entitled.authorization("DERIV", "missing-pack").reason is (
        AuthorizationReason.ENTITLEMENT_MISSING
    )
