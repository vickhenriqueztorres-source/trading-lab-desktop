from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from apps.auth_agent.fake_service import (
    FakeIdentityServiceError,
    FakeIdentityServiceErrorCode,
)
from packages.identity import (
    DeviceIdentityManager,
    LoginChallenge,
    OtpCode,
    PkceMaterial,
    SessionTokens,
)
from packages.licensing import (
    AuthorizationDecision,
    AuthorizationReason,
    LeaseVerifier,
    SignedLease,
)
from packages.observability.events import EventSink, NullEventSink
from packages.security import SecretValue, UserScopedVault

_REFRESH_TOKEN_KEY = "identity.refresh_token"
_USER_ID_KEY = "identity.user_id"
_LEASE_KEY = "licensing.signed_lease"


class IdentityServicePort(Protocol):
    def start_login(self, email: str, pkce_challenge: str) -> dict[str, object]: ...

    def complete_login(
        self, challenge_id: str, code: OtpCode, pkce_verifier: str
    ) -> dict[str, object]: ...

    def refresh_session(self, refresh_token: str) -> dict[str, object]: ...

    def register_device(self, access_token: str, device_id: str, public_key_b64: str) -> None: ...

    def create_device_challenge(self, access_token: str, device_id: str) -> dict[str, object]: ...

    def issue_lease(
        self,
        access_token: str,
        device_id: str,
        challenge_id: str,
        signature_b64: str,
    ) -> dict[str, object]: ...

    def is_lease_revoked(self, lease_id: str) -> bool: ...


class AuthAgentState(StrEnum):
    SIGNED_OUT = "SIGNED_OUT"
    CHALLENGE_PENDING = "CHALLENGE_PENDING"
    AUTHORIZED = "AUTHORIZED"
    OFFLINE_AUTHORIZED = "OFFLINE_AUTHORIZED"
    BLOCKED = "BLOCKED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


@dataclass(frozen=True, slots=True)
class _PendingLogin:
    challenge: LoginChallenge
    pkce: PkceMaterial


class AuthAgent:
    """Owns product identity/session/device/lease, never broker credentials."""

    def __init__(
        self,
        service: IdentityServicePort,
        vault: UserScopedVault,
        lease_verifier: LeaseVerifier,
        *,
        client_version: str = "0.0.1",
        now: Callable[[], datetime] | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._service = service
        self._vault = vault
        self._verifier = lease_verifier
        self._client_version = client_version
        self._now = now or (lambda: datetime.now(UTC))
        self._events = event_sink or NullEventSink()
        self._device = DeviceIdentityManager(vault)
        self._pending: _PendingLogin | None = None
        self._access_token: SecretValue | None = None
        self._signed_lease: SignedLease | None = None
        self._user_id: str | None = None
        self._device_revoked = False
        self._revoked_lease_ids: set[str] = set()
        self.state = AuthAgentState.SIGNED_OUT

    def start_login(self, email: str) -> LoginChallenge:
        pkce = PkceMaterial.create()
        response = self._service.start_login(email, pkce.challenge)
        challenge = self._parse_login_challenge(response)
        self._pending = _PendingLogin(challenge, pkce)
        self._transition(AuthAgentState.CHALLENGE_PENDING, "AUTH_CHALLENGE_STARTED")
        return challenge

    def complete_login(self, code: OtpCode) -> AuthorizationDecision:
        pending = self._pending
        if pending is None:
            raise RuntimeError("no login challenge is pending")
        response = self._service.complete_login(
            pending.challenge.challenge_id,
            code,
            pending.pkce.verifier.reveal_text(),
        )
        tokens = SessionTokens.from_external_payload(response)
        self._pending = None
        self._store_session(tokens)
        self._obtain_and_store_lease(tokens.access_token)
        decision = self.authorization("DERIV", "strategy-test")
        self._transition(
            AuthAgentState.AUTHORIZED if decision.new_entries_allowed else AuthAgentState.BLOCKED,
            decision.reason.value,
        )
        return decision

    def restore(self) -> AuthorizationDecision:
        stored_user = self._vault.load(_USER_ID_KEY)
        stored_lease = self._vault.load(_LEASE_KEY)
        if stored_user is None or stored_lease is None:
            self._transition(
                AuthAgentState.REAUTH_REQUIRED,
                AuthorizationReason.AUTH_REQUIRED.value,
            )
            return self._auth_required()
        try:
            signed = SignedLease.from_bytes(stored_lease.reveal_bytes())
            self._verifier.verify(signed)
            self._device.load_or_create()
        except (ValueError, RuntimeError):
            self._transition(AuthAgentState.BLOCKED, AuthorizationReason.LEASE_INVALID.value)
            return self._blocked(AuthorizationReason.LEASE_INVALID)
        self._user_id = stored_user.reveal_text()
        self._signed_lease = signed
        decision = self.authorization("DERIV", "strategy-test")
        next_state = (
            AuthAgentState.OFFLINE_AUTHORIZED
            if decision.new_entries_allowed
            else AuthAgentState.BLOCKED
        )
        self._transition(next_state, decision.reason.value)
        return decision

    def renew_silently(self) -> AuthorizationDecision:
        refresh = self._vault.load(_REFRESH_TOKEN_KEY)
        if refresh is None:
            self._transition(
                AuthAgentState.REAUTH_REQUIRED,
                AuthorizationReason.AUTH_REQUIRED.value,
            )
            return self._auth_required()
        try:
            response = self._service.refresh_session(refresh.reveal_text())
            tokens = SessionTokens.from_external_payload(response)
            # Persist the rotated token immediately; the prior token is already consumed.
            self._store_session(tokens)
            self._check_current_lease_revocation()
            self._obtain_and_store_lease(tokens.access_token)
        except FakeIdentityServiceError as exc:
            if exc.code is FakeIdentityServiceErrorCode.UNAVAILABLE:
                decision = self.authorization("DERIV", "strategy-test")
                self._transition(
                    AuthAgentState.OFFLINE_AUTHORIZED
                    if decision.new_entries_allowed
                    else AuthAgentState.BLOCKED,
                    exc.code.value if decision.new_entries_allowed else decision.reason.value,
                )
                return decision
            if exc.code is FakeIdentityServiceErrorCode.DEVICE_REVOKED:
                self._device_revoked = True
                reason = AuthorizationReason.DEVICE_REVOKED
            else:
                reason = AuthorizationReason.AUTH_REQUIRED
            self._transition(AuthAgentState.REAUTH_REQUIRED, reason.value)
            return self._blocked(reason)
        decision = self.authorization("DERIV", "strategy-test")
        self._transition(
            AuthAgentState.AUTHORIZED if decision.new_entries_allowed else AuthAgentState.BLOCKED,
            decision.reason.value,
        )
        return decision

    def authorization(
        self,
        broker: str,
        strategy_pack: str,
        *,
        real_mode: bool = False,
    ) -> AuthorizationDecision:
        if self._signed_lease is None or self._user_id is None:
            return self._auth_required()
        try:
            identity = self._device.load_or_create()
        except RuntimeError:
            return self._blocked(AuthorizationReason.DEVICE_MISMATCH)
        return self._verifier.evaluate(
            self._signed_lease,
            now=self._now(),
            expected_user_id=self._user_id,
            expected_device_id=identity.device_id,
            client_version=self._client_version,
            broker=broker,
            strategy_pack=strategy_pack,
            real_mode=real_mode,
            revoked_lease_ids=frozenset(self._revoked_lease_ids),
            device_revoked=self._device_revoked,
        )

    @property
    def current_lease(self) -> SignedLease | None:
        return self._signed_lease

    @property
    def user_id_preview(self) -> str | None:
        if self._user_id is None:
            return None
        return hashlib.sha256(self._user_id.encode("utf-8")).hexdigest()[:12]

    def _store_session(self, tokens: SessionTokens) -> None:
        self._user_id = tokens.user_id
        self._access_token = tokens.access_token
        self._vault.store(_USER_ID_KEY, SecretValue.from_text(tokens.user_id))
        self._vault.store(_REFRESH_TOKEN_KEY, tokens.refresh_token)

    def _obtain_and_store_lease(self, access_token: SecretValue) -> None:
        identity = self._device.load_or_create()
        access_text = access_token.reveal_text()
        self._service.register_device(access_text, identity.device_id, identity.public_key_b64)
        response = self._service.create_device_challenge(access_text, identity.device_id)
        challenge_id, nonce = self._parse_device_challenge(response)
        signature = self._device.sign(nonce)
        raw_lease = self._service.issue_lease(
            access_text,
            identity.device_id,
            challenge_id,
            base64.urlsafe_b64encode(signature).decode("ascii"),
        )
        signed = self._parse_signed_lease(raw_lease)
        claims = self._verifier.verify(signed)
        if claims.user_id != self._user_id or claims.device_id != identity.device_id:
            raise ValueError("issued lease identity mismatch")
        self._signed_lease = signed
        self._vault.store(_LEASE_KEY, SecretValue(signed.to_bytes()))

    def _check_current_lease_revocation(self) -> None:
        if self._signed_lease is None:
            return
        claims = self._verifier.verify(self._signed_lease)
        if self._service.is_lease_revoked(claims.lease_id):
            self._revoked_lease_ids.add(claims.lease_id)

    @staticmethod
    def _parse_login_challenge(response: Mapping[str, object]) -> LoginChallenge:
        if set(response) != {"challenge_id", "expires_at"}:
            raise ValueError("login challenge schema is invalid")
        challenge_id = response["challenge_id"]
        expires_at = response["expires_at"]
        if not isinstance(challenge_id, str) or not isinstance(expires_at, str):
            raise ValueError("login challenge fields are invalid")
        try:
            parsed_expiry = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise ValueError("login challenge expiry is invalid") from exc
        return LoginChallenge(challenge_id, parsed_expiry)

    @staticmethod
    def _parse_device_challenge(response: Mapping[str, object]) -> tuple[str, bytes]:
        if set(response) != {"challenge_id", "nonce_b64", "expires_at"}:
            raise ValueError("device challenge schema is invalid")
        challenge_id = response["challenge_id"]
        nonce_b64 = response["nonce_b64"]
        expires_at = response["expires_at"]
        if (
            not isinstance(challenge_id, str)
            or not isinstance(nonce_b64, str)
            or not isinstance(expires_at, str)
        ):
            raise ValueError("device challenge fields are invalid")
        try:
            datetime.fromisoformat(expires_at)
            nonce = base64.b64decode(nonce_b64, altchars=b"-_", validate=True)
        except ValueError as exc:
            raise ValueError("device challenge encoding is invalid") from exc
        if len(nonce) != 32:
            raise ValueError("device challenge nonce has invalid size")
        return challenge_id, nonce

    @staticmethod
    def _parse_signed_lease(response: Mapping[str, object]) -> SignedLease:
        if set(response) != {"key_id", "payload_b64", "signature_b64"}:
            raise ValueError("signed lease response schema is invalid")
        key_id = response["key_id"]
        payload_b64 = response["payload_b64"]
        signature_b64 = response["signature_b64"]
        if (
            not isinstance(key_id, str)
            or not isinstance(payload_b64, str)
            or not isinstance(signature_b64, str)
        ):
            raise ValueError("signed lease response fields are invalid")
        return SignedLease(
            key_id=key_id,
            payload_b64=payload_b64,
            signature_b64=signature_b64,
        )

    def _transition(self, state: AuthAgentState, reason_code: str) -> None:
        previous = self.state
        self.state = state
        self._events.emit(
            "auth_state_changed",
            reason_code=reason_code,
            state_from=previous.value,
            state_to=state.value,
        )

    @staticmethod
    def _blocked(reason: AuthorizationReason) -> AuthorizationDecision:
        return AuthorizationDecision(False, True, True, reason, None, None)

    @classmethod
    def _auth_required(cls) -> AuthorizationDecision:
        return cls._blocked(AuthorizationReason.AUTH_REQUIRED)
