from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from apps.core.deriv_telemetry import DerivTelemetrySnapshot
from apps.core.digit_risk_config import DigitRiskConfig
from apps.core.readiness import TradingReadinessSnapshot
from apps.core.runtime import CoreRuntime
from apps.core.worker_supervisor import WorkerHealthState
from packages.observability.diagnostic import DiagnosticBundleResult
from packages.protocol import (
    PROTOCOL_VERSION,
    BrokerCardStatus,
    EndpointRole,
    Envelope,
    HealthGateStatus,
    MessageType,
    OrderSummary,
    ProtocolError,
    ProtocolErrorCode,
    UiAccountMode,
    UiCommandAck,
    UiDerivAssetRank,
    UiDerivStrategyStatus,
    UiDigitRiskConfig,
    UiDigitRiskConfigStatus,
    UiGenerateDiagnosticResponse,
    UiGlobalState,
    UiHandshakeRequest,
    UiHandshakeResponse,
    UiHandshakeStatus,
    UiProjectionSnapshot,
    UiUpdateDigitRiskConfigAck,
    UiUpdateDigitRiskConfigCommand,
    require_empty_payload,
)
from packages.protocol.codec import encode_envelope
from packages.protocol.transport import FramedSocket
from packages.security import SecretValue

_CORE_VERSION = "1.0.0"
_MAX_CACHE = 128
_RISK_LOCKED_STATES = {"UNKNOWN", "SETTLEMENT_UNKNOWN"}
_RISK_LOCK_REASONS = {
    "HG_COOLDOWN_ACTIVE",
    "HG_DAILY_STOP_REACHED",
    "HG_DAILY_TAKE_PROFIT_REACHED",
    "HG_ORDER_EVENT_CONFLICT",
    "HG_ORDER_EVENT_GAP",
    "HG_RECONCILIATION_CONFLICT",
    "HG_SETTLEMENT_REQUIRED",
    "HG_SETTLEMENT_UNKNOWN",
    "HG_ORDER_UNKNOWN",
    "HG_RECONCILIATION_UNAVAILABLE",
}
_RECONCILING_REASONS = {"HG_RECONCILIATION_REQUIRED"}
_HEALTH_DESCRIPTIONS = {
    "HG_SAFE_STOP": (
        "Novas entradas pausadas pelo operador; ordens existentes continuam acompanhadas."
    ),
    "HG_ORDER_UNKNOWN": (
        "Existe ordem com resultado de submissão desconhecido aguardando reconciliação."
    ),
    "HG_SETTLEMENT_UNKNOWN": (
        "Existe liquidação sem evidência conclusiva; a exposição permanece reservada."
    ),
    "HG_RECONCILIATION_REQUIRED": (
        "A reconciliação financeira deve terminar antes de novas entradas."
    ),
    "HG_RECONCILIATION_UNAVAILABLE": "A fonte de evidência para reconciliação está indisponível.",
    "HG_WORKER_DISCONNECTED": (
        "O worker financeiro está desconectado; novas entradas estão bloqueadas."
    ),
    "HG_WORKER_NOT_READY": "O worker financeiro ainda não está pronto.",
    "HG_DAILY_STOP_REACHED": "O Stop Loss diário foi atingido; novas entradas estão bloqueadas.",
    "HG_DAILY_TAKE_PROFIT_REACHED": (
        "A meta diária de lucro foi atingida; novas entradas estão bloqueadas para proteger "
        "o lucro."
    ),
    "HG_COOLDOWN_ACTIVE": "Pausa obrigatória pós-perda ativa.",
}


def _to_ui_digit_config(config: DigitRiskConfig) -> UiDigitRiskConfig:
    return UiDigitRiskConfig(
        stake_minor_units=config.stake_minor_units,
        daily_stop_loss_minor_units=config.daily_stop_loss_minor_units,
        daily_take_profit_minor_units=config.daily_take_profit_minor_units,
        max_consecutive_losses=config.max_consecutive_losses,
        cooldown_seconds_after_loss=config.cooldown_seconds_after_loss,
        min_quantum_confidence_pct=config.min_quantum_confidence_pct,
        selected_symbol=config.selected_symbol,
        currency=config.currency,
        auto_select_symbol=config.auto_select_symbol,
        active_strategy_id=config.active_strategy_id,
        martingale_enabled=config.martingale_enabled,
        martingale_multiplier=config.martingale_multiplier,
        martingale_max_steps=config.martingale_max_steps,
        martingale_max_stake_minor_units=config.martingale_max_stake_minor_units,
    )


def _from_ui_digit_config(config: UiDigitRiskConfig) -> DigitRiskConfig:
    return DigitRiskConfig(
        stake_minor_units=config.stake_minor_units,
        daily_stop_loss_minor_units=config.daily_stop_loss_minor_units,
        daily_take_profit_minor_units=config.daily_take_profit_minor_units,
        max_consecutive_losses=config.max_consecutive_losses,
        cooldown_seconds_after_loss=config.cooldown_seconds_after_loss,
        min_quantum_confidence_pct=config.min_quantum_confidence_pct,
        selected_symbol=config.selected_symbol,
        currency=config.currency,
        auto_select_symbol=config.auto_select_symbol,
        active_strategy_id=config.active_strategy_id,
        martingale_enabled=config.martingale_enabled,
        martingale_multiplier=config.martingale_multiplier,
        martingale_max_steps=config.martingale_max_steps,
        martingale_max_stake_minor_units=config.martingale_max_stake_minor_units,
    )


def _response(request: Envelope, kind: MessageType, payload: dict[str, object]) -> Envelope:
    return Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=str(uuid4()),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        source=EndpointRole.CORE,
        target=EndpointRole.UI,
        message_type=kind,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload=payload,
    )


def _error(request: Envelope, reason: str) -> Envelope:
    return _response(
        request,
        MessageType.ERROR,
        {"reason_code": reason, "request_message_id": request.message_id},
    )


class CoreUiProjectionBuilder:
    """Build immutable UI snapshots from Core-owned read models only."""

    def __init__(
        self,
        runtime: CoreRuntime,
        *,
        deriv_health: Callable[[], WorkerHealthState | None],
        deriv_telemetry: Callable[[], DerivTelemetrySnapshot | None] = lambda: None,
        deriv_bot_reason: Callable[[], str] = lambda: "BOT_WAITING_FOR_LIVE_DERIV",
        iqoption_health: Callable[[], WorkerHealthState | None] = lambda: None,
    ) -> None:
        self._runtime = runtime
        self._deriv_health = deriv_health
        self._deriv_telemetry = deriv_telemetry
        self._deriv_bot_reason = deriv_bot_reason
        self._iqoption_health = iqoption_health

    def trading_readiness(self) -> TradingReadinessSnapshot:
        gate = self._runtime.health_gate.get_snapshot()
        deriv_state = self._deriv_health()
        deriv = self._deriv_telemetry()
        frequency = None if deriv is None else deriv.digit_frequency
        broker_process_ready = deriv_state is WorkerHealthState.READY
        broker_authenticated = bool(
            deriv is not None
            and deriv.connected
            and deriv.connection_mode
            in {"DEMO_AUTH_READ_ONLY", "REAL_AUTH_READ_ONLY", "DEMO", "REAL"}
        )
        reconciliation_complete = not any(
            str(item.get("broker")) == "DERIV"
            for item in self._runtime.reader.list_reconciliation_candidates()
        )
        risk_blockers = {
            "HG_COOLDOWN_ACTIVE",
            "HG_DAILY_STOP_REACHED",
            "HG_DAILY_TAKE_PROFIT_REACHED",
            "HG_SETTLEMENT_UNKNOWN",
            "HG_ORDER_UNKNOWN",
            "HG_RECONCILIATION_CONFLICT",
        }
        risk_ready = not any(item in risk_blockers for item in gate.active_blockers)
        clock_trusted = bool(
            deriv is not None and deriv.clock is not None and deriv.clock.is_synced
        )
        market_healthy = bool(
            deriv is not None
            and deriv.connected
            and "HG_MARKET_DATA_DISCONNECTED" not in gate.active_blockers
            and "MD_CLOCK_UNTRUSTED" not in gate.active_blockers
        )
        warmup_complete = bool(frequency is not None and frequency.total_ticks >= 500)
        safe_stop = self._runtime.safe_stop_active
        armed = not safe_stop and self._runtime.dispatcher_started
        order_in_flight = any(
            str(item.get("broker")) == "DERIV"
            for item in self._runtime.reader.list_nonterminal_orders()
        )
        semantic_blockers: list[str] = list(gate.active_blockers)
        prerequisites = (
            (broker_process_ready, "BROKER_PROCESS_NOT_READY"),
            (broker_authenticated, "BROKER_NOT_AUTHENTICATED"),
            (reconciliation_complete, "RECONCILIATION_INCOMPLETE"),
            (risk_ready, "RISK_NOT_READY"),
            (clock_trusted, "CLOCK_NOT_TRUSTED"),
            (market_healthy, "MARKET_NOT_HEALTHY"),
            (warmup_complete, "WARMUP_INCOMPLETE"),
        )
        semantic_blockers.extend(reason for ready, reason in prerequisites if not ready)
        ready_to_arm = all(ready for ready, _reason in prerequisites) and not order_in_flight
        return TradingReadinessSnapshot(
            core_available=True,
            broker_process_ready=broker_process_ready,
            broker_authenticated=broker_authenticated,
            reconciliation_complete=reconciliation_complete,
            risk_ready=risk_ready,
            clock_trusted=clock_trusted,
            market_healthy=market_healthy,
            warmup_complete=warmup_complete,
            safe_stop=safe_stop,
            armed=armed,
            order_in_flight=order_in_flight,
            ready_to_arm=ready_to_arm,
            ready_to_trade=ready_to_arm and armed,
            blocking_reasons=tuple(sorted(set(semantic_blockers))),
        )

    def snapshot(self) -> UiProjectionSnapshot:
        self._runtime.risk_ledger.refresh_digit_health_gate(self._runtime.health_gate)
        gate_snapshot = self._runtime.health_gate.get_snapshot()
        global_gate = gate_snapshot.global_state
        orders = tuple(self._orders())
        safe_stop = self._runtime.safe_stop_active
        global_state = self._global_state(
            gate_open=global_gate.is_open,
            reason=global_gate.reason_code,
            safe_stop=safe_stop,
            orders=orders,
        )
        pnl_by_currency = self._runtime.reader.daily_realized_pnl_by_currency(
            since_utc=datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        )
        if len(pnl_by_currency) == 1:
            pnl_currency, pnl_minor = next(iter(pnl_by_currency.items()))
        else:
            pnl_currency, pnl_minor = None, 0
        simulated = self._runtime.worker_supervisor
        simulated_ready = (
            simulated is not None and simulated.health_state is WorkerHealthState.READY
        )
        deriv_state = self._deriv_health()
        deriv = self._deriv_telemetry()
        deriv_balance = None if deriv is None else deriv.balance
        deriv_clock = None if deriv is None else deriv.clock
        deriv_demo = deriv is not None and deriv.connection_mode in {
            "DEMO_AUTH_READ_ONLY",
            "DEMO",
        }
        deriv_real = deriv is not None and deriv.connection_mode in {
            "REAL_AUTH_READ_ONLY",
            "REAL",
        }

        iq_state = self._iqoption_health()
        iq_connected = iq_state is WorkerHealthState.READY

        cards = [
            BrokerCardStatus(
                broker="SIMULATED",
                account_mode=UiAccountMode.PRACTICE,
                is_connected=simulated_ready,
                balance_minor_units=None,
                currency=None,
                clock_synced=False,
                connection_label="FAKE_SIMULATED",
                clock_latency_ms=None,
            ),
            BrokerCardStatus(
                broker="DERIV",
                account_mode=(
                    UiAccountMode.REAL
                    if deriv_real
                    else UiAccountMode.PRACTICE
                    if deriv_demo
                    else UiAccountMode.DEMO_READ_ONLY
                ),
                is_connected=(
                    deriv.connected if deriv is not None else deriv_state is WorkerHealthState.READY
                ),
                balance_minor_units=(
                    None if deriv_balance is None else deriv_balance.balance_minor_units
                ),
                currency=None if deriv_balance is None else deriv_balance.currency,
                clock_synced=deriv_clock is not None and deriv_clock.is_synced,
                connection_label=(
                    "PUBLIC_READ_ONLY"
                    if deriv is None
                    else {
                        "FAKE_SIMULATED": "FAKE SIMULADO",
                        "PUBLIC_LIVE": "PUBLIC LIVE",
                        "DEMO_LIVE": "DEMO LIVE",
                        "REAL_LIVE": "REAL — DINHEIRO REAL",
                    }[deriv.source.value]
                ),
                clock_latency_ms=(
                    None if deriv_clock is None else deriv_clock.round_trip_milliseconds
                ),
            ),
            BrokerCardStatus(
                broker="IQOPTION",
                account_mode=UiAccountMode.PRACTICE,
                is_connected=iq_connected,
                balance_minor_units=1000000 if iq_connected else None,
                currency="USD" if iq_connected else None,
                clock_synced=iq_connected,
                connection_label="PRACTICE",
                clock_latency_ms=45 if iq_connected else None,
            ),
        ]

        health_gates_list: list[HealthGateStatus] = [
            HealthGateStatus(
                gate_name="GLOBAL_ENTRY_GATE",
                is_open=global_gate.is_open,
                reason_code=global_gate.reason_code,
                description=(
                    "Novas entradas habilitadas pelos gates locais."
                    if global_gate.reason_code is None
                    else _HEALTH_DESCRIPTIONS.get(
                        global_gate.reason_code,
                        "Bloqueio operacional ativo; consulte o código para suporte.",
                    )
                ),
            )
        ]
        for (broker, acc_id), scoped_state in sorted(gate_snapshot.scoped_states.items()):
            gate_name = f"{broker}:{acc_id}"[:64]
            health_gates_list.append(
                HealthGateStatus(
                    gate_name=gate_name,
                    is_open=scoped_state.is_open,
                    reason_code=scoped_state.reason_code,
                    description=(
                        f"Broker {broker} ({acc_id}) operacional."
                        if scoped_state.reason_code is None
                        else _HEALTH_DESCRIPTIONS.get(
                            scoped_state.reason_code,
                            f"Bloqueio em {broker} ({acc_id}): {scoped_state.reason_code}",
                        )
                    ),
                )
            )
        readiness = self.trading_readiness()
        readiness_reasons = tuple(
            item for item in readiness.blocking_reasons if item != "HG_SAFE_STOP"
        )
        readiness_reason = (
            None
            if readiness.ready_to_arm
            else readiness_reasons[0]
            if readiness_reasons
            else "READINESS_INCOMPLETE"
        )
        health_gates_list.append(
            HealthGateStatus(
                gate_name="DERIV_READY_TO_ARM",
                is_open=readiness.ready_to_arm,
                reason_code=readiness_reason,
                description=(
                    "Recuperação concluída; o usuário pode ligar o bot."
                    if readiness.ready_to_arm
                    else f"Pré-requisito pendente: {readiness_reason}."
                ),
            )
        )

        global_exposure = 0
        global_max_exposure = 0
        consecutive_losses = 0
        risk_state_str = "NORMAL"
        digit_config: UiDigitRiskConfig | None = None
        cooldown_remaining_seconds = 0
        martingale_step = 0
        next_stake_minor_units = 0
        projected_sequence_loss_minor_units = 0
        try:
            risk_metrics = self._runtime.risk_ledger.get_metrics()
            global_exposure = risk_metrics.global_exposure_minor_units
            global_max_exposure = risk_metrics.global_max_exposure_minor_units
            consecutive_losses = risk_metrics.consecutive_losses
            risk_state_str = risk_metrics.risk_state.value
            digit_metrics = self._runtime.risk_ledger.get_digit_metrics()
            digit_config = _to_ui_digit_config(digit_metrics.active_config)
            cooldown_remaining_seconds = digit_metrics.cooldown_remaining_seconds
            martingale_step = digit_metrics.martingale_step
            next_stake_minor_units = digit_metrics.next_stake_minor_units
            projected_sequence_loss_minor_units = digit_metrics.projected_sequence_loss_minor_units
        except Exception:
            pass

        return UiProjectionSnapshot(
            global_state=global_state,
            safe_stop_active=safe_stop,
            health_gates=tuple(health_gates_list),
            broker_cards=tuple(cards),
            active_orders=orders,
            daily_pnl_minor_units=pnl_minor,
            daily_pnl_currency=pnl_currency,
            global_exposure_minor_units=global_exposure,
            global_max_exposure_minor_units=global_max_exposure,
            consecutive_losses=consecutive_losses,
            risk_state=risk_state_str,
            digit_risk_config=digit_config,
            cooldown_remaining_seconds=cooldown_remaining_seconds,
            digit_frequency=(None if deriv is None else deriv.digit_frequency),
            deriv_strategies=(
                ()
                if deriv is None
                else tuple(
                    UiDerivStrategyStatus(
                        strategy_id=item.strategy_id.value,
                        display_name=item.display_name,
                        markets=item.markets,
                        lifecycle_status=item.lifecycle_status,
                        signal_state=item.signal_state.value,
                        reason_code=item.reason_code,
                        warmup_current=item.warmup_current,
                        warmup_required=item.warmup_required,
                        last_signal_epoch=item.last_signal_epoch,
                        last_signal_symbol=item.last_signal_symbol,
                        last_contract_type=item.last_contract_type,
                        last_direction=item.last_direction,
                        last_barrier=item.last_barrier,
                        estimated_probability_pct=(
                            None
                            if item.estimated_probability_pct is None
                            else str(item.estimated_probability_pct)
                        ),
                        required_probability_pct=(
                            None
                            if item.required_probability_pct is None
                            else str(item.required_probability_pct)
                        ),
                        analysis_latency_microseconds=item.analysis_latency_microseconds,
                    )
                    for item in deriv.synthetic_strategies
                )
            ),
            deriv_asset_ranking=(
                ()
                if deriv is None
                else tuple(
                    UiDerivAssetRank(
                        symbol=item.symbol,
                        state=item.state.value,
                        reason_code=item.reason_code,
                        warmup_current=item.warmup_current,
                        warmup_required=item.warmup_required,
                        selected=item.selected,
                        strategy_id=(None if item.strategy_id is None else item.strategy_id.value),
                        contract_type=item.contract_type,
                        barrier=item.barrier,
                        estimated_probability_pct=(
                            None
                            if item.estimated_probability_pct is None
                            else str(item.estimated_probability_pct)
                        ),
                        required_probability_pct=(
                            None
                            if item.required_probability_pct is None
                            else str(item.required_probability_pct)
                        ),
                        conservative_margin_pct=(
                            None
                            if item.conservative_margin_pct is None
                            else str(item.conservative_margin_pct)
                        ),
                        analysis_latency_microseconds=item.analysis_latency_microseconds,
                    )
                    for item in deriv.asset_ranking
                )
            ),
            digit_martingale_step=martingale_step,
            digit_next_stake_minor_units=next_stake_minor_units,
            digit_projected_sequence_loss_minor_units=(projected_sequence_loss_minor_units),
            deriv_bot_reason=self._deriv_bot_reason(),
        )

    def _orders(self) -> list[OrderSummary]:
        result: list[OrderSummary] = []
        for row in self._runtime.reader.ui_order_summaries(limit=50):
            broker_order_id = row.get("broker_order_id")
            result.append(
                OrderSummary(
                    order_id=str(row["order_id"]),
                    broker=str(row["broker"]),
                    symbol=str(row["symbol"]),
                    direction=str(row["direction"]),
                    amount_minor_units=int(row["amount_minor"]),
                    currency=str(row["currency"]),
                    state=str(row["state"]),
                    created_at_utc=datetime.fromisoformat(str(row["created_at"])),
                    broker_order_id=str(broker_order_id) if broker_order_id is not None else None,
                    realized_pnl_minor_units=(
                        int(row["realized_pnl_minor"])
                        if row.get("realized_pnl_minor") is not None
                        else None
                    ),
                )
            )
        return result

    @staticmethod
    def _global_state(
        *,
        gate_open: bool,
        reason: str | None,
        safe_stop: bool,
        orders: tuple[OrderSummary, ...],
    ) -> UiGlobalState:
        states = {item.state for item in orders}
        if states & _RISK_LOCKED_STATES or reason in _RISK_LOCK_REASONS:
            return UiGlobalState.RISK_LOCKED
        if reason in _RECONCILING_REASONS:
            return UiGlobalState.RECONCILING
        if safe_stop and reason == "HG_SAFE_STOP":
            return UiGlobalState.SAFE_STOPPED
        return UiGlobalState.READY if gate_open else UiGlobalState.DEGRADED


class CoreUiProjectionService:
    """Authenticated loopback UI endpoint; the UI remains a disposable projection client."""

    def __init__(
        self,
        session_token: SecretValue,
        snapshot_provider: Callable[[], UiProjectionSnapshot],
        safe_stop: Callable[[], None],
        resume: Callable[[], bool],
        shutdown_requested: Callable[[], None],
        diagnostic_provider: Callable[[], DiagnosticBundleResult] | None = None,
        deriv_demo_connect: Callable[[], tuple[bool, str]] | None = None,
        digit_risk_config_update: (
            Callable[[DigitRiskConfig], tuple[bool, str | None]] | None
        ) = None,
        *,
        request_timeout: float = 2.0,
    ) -> None:
        token = session_token.reveal_text()
        if len(token) != 64 or request_timeout <= 0:
            raise ValueError("UI service configuration is invalid")
        bytes.fromhex(token)
        self._token = session_token
        self._snapshot_provider = snapshot_provider
        self._safe_stop = safe_stop
        self._resume = resume
        self._shutdown_requested = shutdown_requested
        self._diagnostic_provider = diagnostic_provider
        self._deriv_demo_connect = deriv_demo_connect
        self._digit_risk_config_update = digit_risk_config_update
        self._request_timeout = request_timeout
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(2)
        self._listener.settimeout(0.25)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_lock = threading.Lock()
        self._active: FramedSocket | None = None
        self._cache: OrderedDict[str, tuple[bytes, Envelope]] = OrderedDict()

    @property
    def port(self) -> int:
        return int(self._listener.getsockname()[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.serve_forever, name="core-ui-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._active_lock:
            if self._active is not None:
                self._active.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def serve_forever(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    connection, address = self._listener.accept()
                except TimeoutError:
                    continue
                if address[0] != "127.0.0.1":
                    connection.close()
                    continue
                self._serve_connection(FramedSocket(connection))
        finally:
            self._listener.close()

    def _serve_connection(self, transport: FramedSocket) -> None:
        with self._active_lock:
            self._active = transport
        try:
            transport.set_timeout(self._request_timeout)
            if not self._authenticate(transport):
                return
            while not self._stop.is_set():
                try:
                    request = transport.receive()
                except (OSError, ProtocolError):
                    return
                try:
                    self._validate_request(request)
                    response = self._dispatch_cached(request)
                except (ProtocolError, RuntimeError, ValueError):
                    response = _error(request, ProtocolErrorCode.UI_IPC_INVALID_MESSAGE.value)
                try:
                    transport.send(response)
                except ProtocolError:
                    return
        finally:
            with self._active_lock:
                if self._active is transport:
                    self._active = None
            transport.close()

    def _authenticate(self, transport: FramedSocket) -> bool:
        try:
            request = transport.receive()
            if (
                request.protocol_version != PROTOCOL_VERSION
                or request.source is not EndpointRole.UI
                or request.target is not EndpointRole.CORE
                or request.message_type is not MessageType.UI_HANDSHAKE_REQUEST
                or request.deadline_at is None
                or request.deadline_at <= datetime.now(UTC)
            ):
                return False
            handshake = UiHandshakeRequest.from_payload(request.payload)
        except ProtocolError:
            return False
        if not hmac.compare_digest(
            handshake.session_token.reveal_bytes(), self._token.reveal_bytes()
        ):
            denied = UiHandshakeResponse(UiHandshakeStatus.DENIED, _CORE_VERSION, None, None)
            transport.send(
                _response(request, MessageType.UI_HANDSHAKE_RESPONSE, denied.to_payload())
            )
            return False
        server_nonce = secrets.token_hex(32)
        proof = hmac.new(
            self._token.reveal_bytes(),
            f"{handshake.client_nonce}:{server_nonce}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        accepted = UiHandshakeResponse(UiHandshakeStatus.OK, _CORE_VERSION, server_nonce, proof)
        transport.send(_response(request, MessageType.UI_HANDSHAKE_RESPONSE, accepted.to_payload()))
        return True

    @staticmethod
    def _validate_request(request: Envelope) -> None:
        if (
            request.protocol_version != PROTOCOL_VERSION
            or request.source is not EndpointRole.UI
            or request.target is not EndpointRole.CORE
            or request.message_type is MessageType.UI_HANDSHAKE_REQUEST
            or request.deadline_at is None
            or request.deadline_at <= datetime.now(UTC)
        ):
            raise ProtocolError(
                ProtocolErrorCode.UI_IPC_INVALID_MESSAGE, "UI request envelope rejected"
            )

    def _dispatch_cached(self, request: Envelope) -> Envelope:
        fingerprint = hashlib.sha256(encode_envelope(request)).digest()
        cached = self._cache.get(request.message_id)
        if cached is not None:
            previous, response = cached
            if not hmac.compare_digest(previous, fingerprint):
                raise ProtocolError(
                    ProtocolErrorCode.UI_IPC_DUPLICATE_CONFLICT,
                    "UI message replay conflict",
                )
            return response
        response = self._dispatch(request)
        self._cache[request.message_id] = (fingerprint, response)
        while len(self._cache) > _MAX_CACHE:
            self._cache.popitem(last=False)
        return response

    def _dispatch(self, request: Envelope) -> Envelope:
        if request.message_type is MessageType.UI_PROJECTION_REQUEST:
            require_empty_payload(request.payload)
            return _response(
                request,
                MessageType.UI_PROJECTION_SNAPSHOT,
                self._snapshot_provider().to_payload(),
            )
        if request.message_type is MessageType.UI_SAFE_STOP_COMMAND:
            require_empty_payload(request.payload)
            self._safe_stop()
            ack = UiCommandAck(True, "SAFE_STOP_ACTIVE", True)
            return _response(request, MessageType.UI_SAFE_STOP_ACK, ack.to_payload())
        if request.message_type is MessageType.UI_RESUME_COMMAND:
            require_empty_payload(request.payload)
            accepted = self._resume()
            ack = UiCommandAck(
                accepted,
                "ENTRIES_RESUMED" if accepted else "OTHER_HEALTH_BLOCKER_ACTIVE",
                False,
            )
            return _response(request, MessageType.UI_RESUME_ACK, ack.to_payload())
        if request.message_type is MessageType.UI_SHUTDOWN_REQUEST:
            require_empty_payload(request.payload)
            self._safe_stop()
            self._shutdown_requested()
            ack = UiCommandAck(True, "SAFE_SHUTDOWN_REQUESTED", True)
            return _response(request, MessageType.UI_SHUTDOWN_ACK, ack.to_payload())
        if request.message_type is MessageType.UI_GENERATE_DIAGNOSTIC_COMMAND:
            require_empty_payload(request.payload)
            if self._diagnostic_provider is None:
                resp = UiGenerateDiagnosticResponse(
                    success=False,
                    bundle_path=None,
                    sha256_hash=None,
                    file_size_bytes=0,
                    reason_code="DIAGNOSTIC_SERVICE_UNAVAILABLE",
                )
                return _response(
                    request,
                    MessageType.UI_GENERATE_DIAGNOSTIC_RESPONSE,
                    resp.to_payload(),
                )
            try:
                result = self._diagnostic_provider()
                resp = UiGenerateDiagnosticResponse(
                    success=True,
                    bundle_path=str(result.zip_path),
                    sha256_hash=result.sha256_hash,
                    file_size_bytes=result.file_size_bytes,
                    reason_code=None,
                )
            except Exception as exc:
                reason = getattr(exc, "reason_code", "DIAGNOSTIC_GENERATION_FAILED")
                resp = UiGenerateDiagnosticResponse(
                    success=False,
                    bundle_path=None,
                    sha256_hash=None,
                    file_size_bytes=0,
                    reason_code=str(reason),
                )
            return _response(
                request,
                MessageType.UI_GENERATE_DIAGNOSTIC_RESPONSE,
                resp.to_payload(),
            )
        if request.message_type is MessageType.UI_DERIV_DEMO_CONNECT_COMMAND:
            require_empty_payload(request.payload)
            if self._deriv_demo_connect is None:
                ack = UiCommandAck(False, "DERIV_DEMO_CONNECT_UNAVAILABLE", self._safe_stop_state())
            else:
                accepted, reason = self._deriv_demo_connect()
                ack = UiCommandAck(accepted, reason, self._safe_stop_state())
            return _response(request, MessageType.UI_DERIV_DEMO_CONNECT_ACK, ack.to_payload())
        if request.message_type is MessageType.UI_UPDATE_DIGIT_RISK_CONFIG_COMMAND:
            command = UiUpdateDigitRiskConfigCommand.from_payload(request.payload)
            if self._digit_risk_config_update is None:
                digit_ack = UiUpdateDigitRiskConfigAck(
                    UiDigitRiskConfigStatus.REJECTED,
                    "DIGIT_RISK_CONFIG_UPDATE_UNAVAILABLE",
                )
            else:
                accepted, reason = self._digit_risk_config_update(
                    _from_ui_digit_config(command.config)
                )
                digit_ack = UiUpdateDigitRiskConfigAck(
                    (UiDigitRiskConfigStatus.OK if accepted else UiDigitRiskConfigStatus.REJECTED),
                    None if accepted else reason or "DIGIT_RISK_CONFIG_REJECTED",
                )
            return _response(
                request,
                MessageType.UI_UPDATE_DIGIT_RISK_CONFIG_ACK,
                digit_ack.to_payload(),
            )
        raise ProtocolError(
            ProtocolErrorCode.UI_IPC_INVALID_MESSAGE, "UI message type is unsupported"
        )

    def _safe_stop_state(self) -> bool:
        try:
            return self._snapshot_provider().safe_stop_active
        except Exception:
            return True
