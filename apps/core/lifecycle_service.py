from __future__ import annotations

import os
import random
import sys
import threading
import time
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from apps.auth_agent.core_gate import CoreLeaseEntryAuthorizer, DerivTokenEntryAuthorizer
from apps.core.auth_supervisor import AuthAgentSupervisor
from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import (
    DerivTelemetryMonitor,
    DerivTelemetrySource,
)
from apps.core.digit_risk_config import DigitRiskConfig, StrategySelectionMode
from apps.core.execution_state import (
    ExecutionState,
    OperatorIntentStore,
    StopReason,
    TransportSupervisor,
)
from apps.core.iqoption_auto_trader import (
    IQOPTION_CLOCK_FAILURE_REASONS,
    IQOPTION_PRACTICE_ACCOUNT_ID,
    IqOptionAutoTrader,
)
from apps.core.iqoption_candidates import TIMEFRAMES
from apps.core.iqoption_connection_safety import (
    IQOptionConnectionSafetyController,
    IQOptionConnectionSafetyStateError,
    IQOptionConnectionSafetyStore,
    IQOptionMessageBudget,
    assert_safe_stop_caller,
)
from apps.core.iqoption_risk_config import IqOptionRiskConfig, IqOptionRiskConfigStore
from apps.core.live_monitor import LiveMonitor
from apps.core.manifest_catalog import DynamicManifestCatalog
from apps.core.manifest_client import (
    DEFAULT_CONSUMER_CAPABILITIES,
    DEFAULT_MANIFEST_MIRROR_URL,
    DEFAULT_MANIFEST_PRIMARY_URL,
    DEFAULT_PARITY_SHA256,
    HttpTransportProtocol,
    ManifestClient,
    ManifestRecord,
    ManifestRefreshService,
    UrlLibManifestTransport,
    evaluate_manifest_bytes,
)
from apps.core.manifest_keys import PROD_PUBLIC_KEYS
from apps.core.outcomes_uploader import OutcomesUploader
from apps.core.payout_routed_differs import (
    PAYOUT_ROUTED_DIFFERS_STRATEGY_ID,
    PayoutRoutedDiffersProposalCache,
    PayoutRoutedDiffersQuoteFeeder,
)
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec, ReadOnlyWorkerSupervisor
from apps.core.runtime import CoreRuntime
from apps.core.ui_service import CoreUiProjectionBuilder, CoreUiProjectionService
from apps.core.worker_client import WorkerDispatchError
from apps.core.worker_supervisor import WorkerHealthState
from packages.brokers.deriv.credentials import DerivCredentialVault
from packages.brokers.iqoption.credentials import IQOptionCredentialVault
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot
from packages.domain.models import Broker
from packages.protocol import EndpointRole, LifecycleProcessStatus, ProtocolError, ProtocolErrorCode
from packages.security import SecretValue


class CoreServiceState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    SAFE_STOP = "SAFE_STOP"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


_DEMO_TEST_SESSION_BLOCKERS = frozenset(
    {
        "HG_DAILY_STOP_REACHED",
        "HG_DAILY_TAKE_PROFIT_REACHED",
        "HG_COOLDOWN_ACTIVE",
    }
)

# PyInstaller cold starts on Windows can take materially longer than the source
# runtime, especially when launched from the portable wrapper. Keep the IQ
# worker handshake bounded but generous enough to avoid a false timeout before
# the subprocess reaches its loopback listener.
_IQOPTION_WORKER_HANDSHAKE_TIMEOUT_SECONDS = 45.0
_IQOPTION_WORKER_RESPONSE_TIMEOUT_SECONDS = 65.0
_IQOPTION_WORKER_HEARTBEAT_TIMEOUT_SECONDS = 30.0
_IQOPTION_RECOVERY_DELAYS_SECONDS = (0.0, 5.0, 15.0, 30.0, 60.0)


class CoreLifecycleService:
    """Core-owned composition used by the Launcher; no financial state enters the Launcher."""

    def __init__(
        self,
        profile_dir: Path,
        workers: tuple[str, ...],
        *,
        force_auth_simulation: bool = False,
        ui_session_token: SecretValue | None = None,
        deriv_transport: str = "fake-public",
        manifest_http: HttpTransportProtocol | None = None,
        manifest_remote_enabled: bool | None = None,
    ) -> None:
        if "simulated" not in workers:
            raise ValueError("the Phase 1 Core requires the simulated financial worker")
        if len(workers) != len(set(workers)) or not set(workers) <= {
            "simulated",
            "deriv_read_only",
            "iqoption",
        }:
            raise ValueError("worker selection is invalid")
        if deriv_transport not in {
            "fake-public",
            "fake-demo",
            "live-public",
            "live-demo",
            "live-real",
        }:
            raise ValueError("Deriv transport selection is invalid")
        self._profile_dir = Path(profile_dir)
        self._workers = workers
        self._auth = AuthAgentSupervisor(
            self._profile_dir / "auth",
            force_simulation=force_auth_simulation,
            allow_real_mode=True,
        )
        self._runtime: CoreRuntime | None = None
        self._deriv: ReadOnlyWorkerSupervisor | None = None
        self._iqoption: ReadOnlyWorkerSupervisor | None = None
        self._iqoption_connecting: ReadOnlyWorkerSupervisor | None = None
        self._iqoption_balance: BrokerAccountBalance | None = None
        self._iqoption_clock: BrokerClockSnapshot | None = None
        self._iqoption_session_invalidated = False
        try:
            self._iqoption_connection_safety: IQOptionConnectionSafetyController | None = (
                IQOptionConnectionSafetyController(
                    IQOptionConnectionSafetyStore(self._profile_dir / "core")
                )
            )
        except IQOptionConnectionSafetyStateError:
            # Corrupt/unwritable protection state must never silently reset the
            # anti-login-storm counters.  Keep the app available, but fail the
            # external IQ Option connection closed until the state is repaired.
            self._iqoption_connection_safety = None
        self._iqoption_risk_store = IqOptionRiskConfigStore(self._profile_dir / "core")
        try:
            self._iqoption_risk_config = self._iqoption_risk_store.load()
        except ValueError:
            self._iqoption_risk_config = IqOptionRiskConfig()
        self._manifest_catalog = DynamicManifestCatalog(event_sink=self._emit_manifest_event)
        self._manifest_load_reason = "MANIFEST_NOT_FOUND"
        self._manifest_bootstrap_raw: bytes | None = None
        self._live_monitor: LiveMonitor | None = None
        self._outcomes_uploader: OutcomesUploader | None = None
        self._load_local_manifest_catalog()
        self._manifest_client = ManifestClient(
            http=manifest_http if manifest_http is not None else UrlLibManifestTransport(),
            cache_dir=self._profile_dir / "cache",
            public_keys=PROD_PUBLIC_KEYS,
            primary_url=os.environ.get(
                "DUALTRADE_MANIFEST_PRIMARY_URL", DEFAULT_MANIFEST_PRIMARY_URL
            ),
            mirror_url=os.environ.get("DUALTRADE_MANIFEST_MIRROR_URL", DEFAULT_MANIFEST_MIRROR_URL),
            capabilities=DEFAULT_CONSUMER_CAPABILITIES,
            on_event=self._emit_manifest_event,
        )
        cached_manifest = self._manifest_client.current()
        if cached_manifest is None and self._manifest_bootstrap_raw is not None:
            self._manifest_client.accept(self._manifest_bootstrap_raw)
            cached_manifest = self._manifest_client.current()
        if (
            cached_manifest is not None
            and self._manifest_catalog.manifest_version != cached_manifest.manifest_version
        ):
            self._manifest_catalog.apply_manifest(cached_manifest)
        self._manifest_client.on_prepare(self._manifest_catalog.prepare_manifest)
        self._manifest_client.on_change(self._on_manifest_applied)
        remote_default = ui_session_token is not None
        self._manifest_remote_enabled = (
            remote_default if manifest_remote_enabled is None else manifest_remote_enabled
        )
        self._manifest_refresh = ManifestRefreshService(
            self._manifest_client,
            on_event=self._emit_manifest_event,
        )
        self._operator_intent_store = OperatorIntentStore(self._profile_dir)
        try:
            self._transport_supervisor = TransportSupervisor(
                intent_store=self._operator_intent_store
            )
            self._operator_intent_invalid = False
        except ValueError:
            self._transport_supervisor = TransportSupervisor(
                intent_store=self._operator_intent_store,
                initially_armed=False,
            )
            self._operator_intent_invalid = True
        self._iqoption_bot_armed = self._transport_supervisor.armed_intent
        self._iqoption_bot_reason = (
            "TRANSPORT_DOWN" if self._iqoption_bot_armed else "IQOPTION_BOT_DISARMED"
        )
        self._iqoption_message_budget = IQOptionMessageBudget()
        self._iqoption_auto_trader = IqOptionAutoTrader(
            supervisor_provider=lambda: self._iqoption,
            runtime_provider=lambda: self._runtime,
            risk_config_provider=lambda: self._iqoption_risk_config,
            operator_armed=lambda: self._iqoption_bot_armed,
            catalog_provider=lambda: self._manifest_catalog,
            account_type_provider=lambda: (
                self._iqoption_balance.account_type
                if self._iqoption_balance is not None
                else "UNKNOWN"
            ),
            monitor_provider=lambda: self._live_monitor,
            recovery_notifier=self._request_iqoption_recovery,
            message_budget=self._iqoption_message_budget,
            transport_supervisor=self._transport_supervisor,
            reconcile_orders=self._schedule_iqoption_reconciliation,
        )
        self._deriv_transport = deriv_transport
        self._deriv_telemetry: DerivTelemetryMonitor | None = None
        self._deriv_auto_trader: DerivDigitAutoTrader | None = None
        self._payout_differs_feeder: PayoutRoutedDiffersQuoteFeeder | None = None
        self._ui_session_token = ui_session_token
        self._ui_service: CoreUiProjectionService | None = None
        self._ui_shutdown_requested = False
        self._state = CoreServiceState.STARTING
        self._safe_stop = False
        self._restart_counts = {"AUTH_AGENT": 0, "DERIV_WORKER": 0}
        self._deriv_switch_lock = threading.RLock()
        self._iqoption_switch_lock = threading.RLock()
        self._deriv_recovery_stop = threading.Event()
        self._iqoption_recovery_stop = threading.Event()
        self._iqoption_recovery_jitter = random.uniform
        self._deriv_recovery_thread: threading.Thread | None = None
        self._iqoption_startup_recovery_thread: threading.Thread | None = None
        self._deriv_generation = 0
        self._deriv_account_id: str | None = None
        self._pending_deriv_recovery_reason: str | None = None
        self._workers_stopped = False
        self._auth_stopped = False
        self._startup_sequence: list[str] = []

    @property
    def state(self) -> CoreServiceState:
        return self._state

    @property
    def safe_stop_active(self) -> bool:
        return self._safe_stop

    @property
    def ui_port(self) -> int:
        if self._ui_service is None:
            raise RuntimeError("CORE_UI_SERVICE_UNAVAILABLE")
        return self._ui_service.port

    @property
    def ui_shutdown_requested(self) -> bool:
        return self._ui_shutdown_requested

    @property
    def startup_sequence(self) -> tuple[str, ...]:
        return tuple(self._startup_sequence)

    def start(self) -> None:
        if self._state is CoreServiceState.READY:
            return
        self._state = CoreServiceState.STARTING
        self._deriv_recovery_stop.clear()
        self._iqoption_recovery_stop.clear()
        try:
            self._auth.start()
            self._startup_sequence.append("AUTH_AGENT")
            runtime = CoreRuntime(
                self._profile_dir / "core",
                deferred_reconciliation_brokers=frozenset({Broker.DERIV, Broker.IQ_OPTION}),
                digit_account_type=(
                    "demo"
                    if self._deriv_transport == "live-demo"
                    else "real"
                    if self._deriv_transport == "live-real"
                    else None
                ),
                entry_authorizer_factory=lambda gate: DerivTokenEntryAuthorizer(
                    CoreLeaseEntryAuthorizer(
                        self._auth,
                        gate,
                        real_mode_resolver=lambda broker, _strategy: (
                            broker.value == "DERIV" and self._deriv_transport == "live-real"
                        ),
                    ),
                    gate,
                    deriv_session_ready=lambda: (
                        self._deriv_transport in {"live-demo", "live-real"}
                        and self._deriv is not None
                    ),
                    iqoption_practice_session_ready=lambda: (
                        self._iqoption is not None
                        and self._iqoption.health_state is WorkerHealthState.READY
                        and self._iqoption_balance is not None
                        and self._iqoption_balance.account_type.upper() in {"DEMO", "PRACTICE"}
                    ),
                ),
            )
            self._startup_sequence.append("CORE")
            runtime.start()
            self._runtime = runtime
            self._emit_manifest_event(
                "manifest_startup_validation", {"reason_code": self._manifest_load_reason}
            )
            if self._operator_intent_invalid:
                runtime.event_sink.emit(
                    "iqoption_operator_intent_rejected",
                    reason_code="IQOPTION_OPERATOR_INTENT_INVALID",
                )
            # Anonymous outcome telemetry is opt-in and strictly off the
            # financial path.  No endpoint means no uploader and no network work.
            telemetry_opt_in = os.environ.get("DUALTRADE_OUTCOMES_OPT_IN") == "1"
            telemetry_endpoint = os.environ.get("DUALTRADE_OUTCOMES_ENDPOINT", "").strip()
            if telemetry_opt_in and telemetry_endpoint.startswith("https://"):
                self._outcomes_uploader = OutcomesUploader(
                    writer=runtime.writer,
                    identity_file=self._profile_dir / "telemetry" / "client_identity.json",
                    endpoint_url=telemetry_endpoint,
                    opt_in=True,
                )
                self._outcomes_uploader.start()
            self._live_monitor = LiveMonitor(
                self._manifest_catalog,
                writer=runtime.writer,
                event_sink=runtime.event_sink,
                uploader=self._outcomes_uploader,
            )
            self._live_monitor.start()
            if self._manifest_remote_enabled:
                self._manifest_refresh.start()
                self._startup_sequence.append("MANIFEST_REFRESH")
            runtime.iqoption_entry_validator = self._iqoption_auto_trader.validate_runtime_entry
            runtime.iqoption_execution_lock = self._manifest_catalog.execution_lock
            runtime.iqoption_order_registered = self._manifest_catalog.notify_order_opened
            # Transport availability cannot overwrite persisted operator intent.
            # A restored ARMED intent remains degraded until a worker is proven up.
            self._safe_stop = True
            self._startup_sequence.append("SIMULATED_WORKER")
            if "deriv_read_only" in self._workers:
                deriv = ReadOnlyWorkerSupervisor(
                    runtime.health_gate,
                    self._deriv_spec(),
                    handshake_timeout=(
                        45.0 if self._deriv_transport in {"live-demo", "live-real"} else 5.0
                    ),
                    response_timeout=(
                        12.0 if self._deriv_transport in {"live-demo", "live-real"} else 2.0
                    ),
                    heartbeat_timeout=(
                        10.0 if self._deriv_transport in {"live-demo", "live-real"} else 1.0
                    ),
                )
                deriv.start()
                self._deriv = deriv
                self._start_deriv_telemetry(runtime, deriv)
                self._activate_deriv_financial_runtime(runtime, deriv)
                self._startup_sequence.append("DERIV_WORKER")
            if self._ui_session_token is not None:
                projection = CoreUiProjectionBuilder(
                    runtime,
                    deriv_health=lambda: None if self._deriv is None else self._deriv.health_state,
                    deriv_telemetry=lambda: (
                        None if self._deriv_telemetry is None else self._deriv_telemetry.snapshot
                    ),
                    deriv_bot_armed=lambda: not self._safe_stop,
                    deriv_bot_reason=lambda: (
                        "BOT_WAITING_FOR_LIVE_DERIV"
                        if self._deriv_auto_trader is None
                        else self._deriv_auto_trader.last_reason
                    ),
                    deriv_bot_waiting_status=lambda: (
                        None
                        if self._deriv_auto_trader is None
                        else self._deriv_auto_trader.waiting_status
                    ),
                    iqoption_health=lambda: (
                        None
                        if self._iqoption is None
                        else WorkerHealthState.DISCONNECTED
                        if self._iqoption_session_invalidated
                        else self._iqoption.health_state
                    ),
                    iqoption_balance=lambda: (
                        None
                        if self._iqoption_session_invalidated
                        else self._iqoption_auto_trader.latest_balance
                        if self._iqoption_auto_trader is not None
                        and self._iqoption_auto_trader.latest_balance is not None
                        else self._iqoption_balance
                    ),
                    iqoption_clock=lambda: (
                        None
                        if self._iqoption_session_invalidated
                        else self._iqoption_auto_trader.latest_clock
                        if self._iqoption_auto_trader is not None
                        else self._iqoption_clock
                    ),
                    iqoption_risk_config=lambda: self._iqoption_risk_config,
                    iqoption_bot_armed=lambda: self._iqoption_bot_armed,
                    iqoption_bot_reason=lambda: (
                        self._iqoption_auto_trader.status_reason
                        if self._iqoption_bot_armed
                        else self._iqoption_bot_reason
                    ),
                    iqoption_asset_ranking=lambda: (
                        self._iqoption_auto_trader.asset_ranking
                        if self._iqoption_auto_trader is not None
                        else ()
                    ),
                    iqoption_execution_metrics=lambda: (
                        self._iqoption_auto_trader.execution_metrics()
                        if self._iqoption_auto_trader is not None
                        else None
                    ),
                )
                ui_service = CoreUiProjectionService(
                    self._ui_session_token,
                    projection.snapshot,
                    lambda: self.safe_stop(caller=StopReason.USER_COMMAND),
                    self.resume,
                    self._request_ui_shutdown,
                    deriv_demo_connect=self.connect_deriv_selected_account,
                    digit_risk_config_update=self._update_digit_risk_config,
                    digit_test_session_reset=self.reset_digit_test_session,
                    iqoption_login=self.connect_iqoption_selected_account,
                    iqoption_risk_config_update=self.update_iqoption_risk_config,
                    iqoption_bot_control=self.control_iqoption_bot,
                )
                ui_service.start()
                self._ui_service = ui_service
                self._iqoption_auto_trader.start()
        except Exception:
            self._state = CoreServiceState.FAILED
            self.emergency_shutdown()
            raise
        self._state = CoreServiceState.READY
        pending_recovery = self._pending_deriv_recovery_reason
        self._pending_deriv_recovery_reason = None
        if pending_recovery is not None:
            self._request_deriv_recovery(pending_recovery)
        # Recovery of a durable nonterminal order must not depend on opening the UI
        # and pressing Connect. It runs in the background while entries stay disarmed.
        runtime = self._require_runtime()
        has_deriv_recovery = any(
            str(item.get("broker")) == Broker.DERIV.value
            for item in runtime.reader.list_reconciliation_candidates()
        )
        self._schedule_saved_deriv_startup(has_deriv_recovery=has_deriv_recovery)
        has_iqoption_recovery = any(
            str(item.get("broker")) == Broker.IQ_OPTION.value
            for item in runtime.reader.list_reconciliation_candidates()
        )
        self._schedule_saved_iqoption_recovery(has_iqoption_recovery=has_iqoption_recovery)

    def _load_local_manifest_catalog(self) -> None:
        repo_data_manifest = Path(__file__).resolve().parents[2] / "data" / "manifest.json"
        profile_cache_manifest = self._profile_dir / "cache" / "manifest.json"
        candidates = [
            profile_cache_manifest,
            Path("cache/manifest.json"),
            Path("data/manifest.json"),
            repo_data_manifest,
        ]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "data" / "manifest.json")
        if getattr(sys, "executable", None):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / "data" / "manifest.json")
            candidates.append(exe_dir / "_internal" / "data" / "manifest.json")
        for path in candidates:
            if path.is_file():
                try:
                    raw = path.read_bytes()
                    data, reason = evaluate_manifest_bytes(
                        raw,
                        PROD_PUBLIC_KEYS,
                        expected_primitives_version="1.0.0",
                        expected_parity_sha256=DEFAULT_PARITY_SHA256,
                        capabilities=DEFAULT_CONSUMER_CAPABILITIES,
                    )
                    if data is None:
                        self._manifest_load_reason = reason
                        self._emit_manifest_event("manifest_rejected", {"reason_code": reason})
                        if path == profile_cache_manifest:
                            return
                        continue
                    self._manifest_catalog.apply_manifest(data)
                    self._manifest_bootstrap_raw = raw
                    self._manifest_load_reason = "MANIFEST_ACCEPTED"
                    return
                except Exception:
                    if path == profile_cache_manifest:
                        self._manifest_load_reason = "MANIFEST_REJECTED"
                        self._emit_manifest_event(
                            "manifest_rejected", {"reason_code": "MANIFEST_REJECTED"}
                        )
                        return
                    continue

    def _schedule_saved_deriv_startup(self, *, has_deriv_recovery: bool) -> None:
        """Reconnect a saved Demo account without ever rearming or auto-selecting Real."""

        if "deriv_read_only" not in self._workers or self._deriv_transport in {
            "live-demo",
            "live-real",
        }:
            return
        try:
            saved = DerivCredentialVault(self._profile_dir / "broker_credentials").load()
        except (OSError, RuntimeError, ValueError):
            return
        if saved is None:
            return
        if saved.account_type == "demo":
            self._deriv_transport = "live-demo"
            self._request_deriv_recovery("DERIV_SAVED_DEMO_AUTO_CONNECT")
        elif saved.account_type == "real" and has_deriv_recovery:
            # Real remains read-only. It is selected automatically only when an
            # existing durable order needs reconciliation, never for new entries.
            self._deriv_transport = "live-real"
            self._request_deriv_recovery("DERIV_STARTUP_RECONCILIATION_REQUIRED")

    def _schedule_saved_iqoption_recovery(self, *, has_iqoption_recovery: bool) -> None:
        """Recover durable orders or a persisted ARMED operator intent."""

        if not has_iqoption_recovery and not self._transport_supervisor.armed_intent:
            return
        try:
            saved_mode = IQOptionCredentialVault(
                self._profile_dir / "broker_credentials"
            ).configured_account_mode()
        except (OSError, RuntimeError, ValueError):
            return
        if saved_mode != "practice":
            # Real remains read-only and is never selected automatically.
            return
        self._request_iqoption_recovery("IQOPTION_STARTUP_RECONCILIATION_REQUIRED")

    def _schedule_iqoption_reconciliation(self) -> None:
        """Ask the existing bounded scheduler to reconcile after transport returns."""

        runtime = self._runtime
        supervisor = self._iqoption
        if runtime is not None and supervisor is not None:
            runtime.reconcile_iqoption_worker(supervisor.client)

    def _request_iqoption_recovery_from(
        self,
        source: ReadOnlyWorkerSupervisor,
        code: ProtocolErrorCode,
    ) -> None:
        """Fence callbacks from a supervisor that is no longer current."""

        if source is self._iqoption:
            self._request_iqoption_recovery(code.value)

    def _request_iqoption_recovery(self, reason_code: str) -> None:
        """Recover transport while preserving the operator's execution intent."""

        if self._state in {CoreServiceState.STOPPING, CoreServiceState.STOPPED}:
            return
        runtime = self._runtime
        if runtime is None or self._iqoption_recovery_stop.is_set():
            return
        with self._iqoption_switch_lock:
            current = self._iqoption_startup_recovery_thread
            self._iqoption_auto_trader.on_transport_down(reason_code)
            if self._transport_supervisor.armed_intent:
                self._iqoption_bot_reason = "TRANSPORT_DOWN"
            # IPC PONG proves only process liveness. A broker failure invalidates
            # cached evidence even when the supervisor still reports READY.
            self._iqoption_session_invalidated = True
            self._iqoption_balance = None
            self._iqoption_clock = None
            if current is not None and current.is_alive():
                return
            runtime.event_sink.emit(
                "iqoption_recovery_requested",
                reason_code=reason_code,
            )
            thread = threading.Thread(
                target=self._iqoption_recovery_loop,
                name="iqoption-connection-recovery",
                daemon=True,
            )
            self._iqoption_startup_recovery_thread = thread
            thread.start()

    def _iqoption_recovery_loop(self) -> None:
        terminal_reasons = {
            "IQOPTION_AUTH_FAILED",
            "IQOPTION_2FA_REQUIRED",
            "IQOPTION_RATE_LIMITED",
            "IQOPTION_CONNECTION_SAFETY_STATE_INVALID",
            "IQOPTION_CREDENTIALS_NOT_CONFIGURED",
            "IQOPTION_SAVED_LOGIN_UNAVAILABLE",
            "IQOPTION_SAVED_REAL_REQUIRES_CONFIRMATION",
        }
        attempt = 0
        while True:
            if self._iqoption_recovery_stop.is_set():
                return
            runtime = self._runtime
            if runtime is None or self._state in {
                CoreServiceState.STOPPING,
                CoreServiceState.STOPPED,
            }:
                return
            base_delay = _IQOPTION_RECOVERY_DELAYS_SECONDS[
                min(attempt, len(_IQOPTION_RECOVERY_DELAYS_SECONDS) - 1)
            ]
            jitter = getattr(self, "_iqoption_recovery_jitter", random.uniform)
            radius = base_delay * 0.10
            delay = max(0.0, base_delay + jitter(-radius, radius)) if radius else base_delay
            if self._iqoption_recovery_stop.wait(delay):
                return
            attempt += 1
            runtime.event_sink.emit(
                "iqoption_recovery_attempt",
                attempt=attempt,
                delay_ms=int(delay * 1000),
            )
            soft_connected, soft_reason, retry_same_worker, retry_after = (
                self._try_reconnect_iqoption_websocket()
            )
            if soft_connected:
                runtime.event_sink.emit(
                    "iqoption_recovery_connected",
                    reason_code="IQOPTION_WEBSOCKET_SESSION_REUSED",
                    attempt=attempt,
                )
                return
            if retry_same_worker:
                runtime.event_sink.emit(
                    "iqoption_recovery_failed",
                    reason_code=soft_reason,
                    attempt=attempt,
                    recovery_layer="WEBSOCKET",
                    retry_after_seconds=int(retry_after),
                )
                if retry_after > 0:
                    if self._iqoption_recovery_stop.wait(retry_after):
                        return
                    attempt = 0
                continue
            controller = self._iqoption_connection_safety
            if controller is not None:
                try:
                    safety = controller.snapshot()
                except IQOptionConnectionSafetyStateError:
                    self._iqoption_connection_safety = None
                    self._iqoption_bot_reason = "IQOPTION_CONNECTION_SAFETY_STATE_INVALID"
                    return
                if safety.quarantine_active:
                    if self._transport_supervisor.armed_intent:
                        self._iqoption_bot_reason = "TRANSPORT_DOWN"
                    else:
                        self._iqoption_bot_reason = "IQOPTION_CONNECTION_QUARANTINED"
                    runtime.event_sink.emit(
                        "iqoption_recovery_waiting",
                        reason_code="IQOPTION_CONNECTION_QUARANTINED",
                        retry_after_seconds=safety.retry_after_seconds,
                    )
                    if self._iqoption_recovery_stop.wait(safety.retry_after_seconds):
                        return
                    continue
            accepted, connected, reason = self.connect_iqoption_selected_account("saved")
            if accepted and connected and not self._iqoption_session_invalidated:
                self._iqoption_bot_reason = (
                    "IQOPTION_BOT_ARMED"
                    if self._transport_supervisor.armed_intent
                    else "IQOPTION_BOT_DISARMED"
                )
                runtime.event_sink.emit(
                    "iqoption_recovery_connected",
                    reason_code="EXECUTION_INTENT_PRESERVED",
                    attempt=attempt,
                )
                return
            if accepted and connected:
                reason = "IQOPTION_BROKER_SESSION_UNAVAILABLE"
            runtime.event_sink.emit(
                "iqoption_recovery_failed",
                reason_code=reason,
                attempt=attempt,
            )
            if reason in terminal_reasons:
                self._iqoption_bot_reason = reason
                return
            keep_recovering = self._transport_supervisor.armed_intent or any(
                str(item.get("broker")) == Broker.IQ_OPTION.value
                for item in runtime.reader.list_nonterminal_orders()
            )
            if keep_recovering or attempt < len(_IQOPTION_RECOVERY_DELAYS_SECONDS):
                continue
            self._iqoption_bot_reason = "TRANSPORT_DOWN"
            runtime.event_sink.emit(
                "iqoption_recovery_exhausted",
                reason_code="TRANSPORT_DOWN",
                attempts=attempt,
            )
            return

    def _try_reconnect_iqoption_websocket(self) -> tuple[bool, str, bool, float]:
        """Reuse the worker's in-memory SSID before considering another HTTP login."""

        supervisor = getattr(self, "_iqoption", None)
        runtime = getattr(self, "_runtime", None)
        if supervisor is None or runtime is None:
            return False, "WORKER_NOT_READY", False, 0.0
        client = supervisor.client
        reconnect = getattr(client, "iqoption_reconnect_session", None)
        if not getattr(client, "is_ready", True) or not callable(reconnect):
            return False, "WORKER_NOT_READY", False, 0.0
        try:
            reconnect()
            balance = client.broker_balance()
            clock: BrokerClockSnapshot | None = None
            try:
                clock = client.broker_clock()
            except WorkerDispatchError as exc:
                if exc.code.value not in IQOPTION_CLOCK_FAILURE_REASONS:
                    raise
                runtime.health_gate.block_scope(
                    Broker.IQ_OPTION.value,
                    IQOPTION_PRACTICE_ACCOUNT_ID,
                    "MD_CLOCK_UNTRUSTED",
                )
                runtime.event_sink.emit(
                    "iqoption_clock_unavailable",
                    broker=Broker.IQ_OPTION.value,
                    reason_code=exc.code.value,
                )
        except WorkerDispatchError as exc:
            reason = exc.code.value
            retry_same = reason not in {
                "IQOPTION_AUTH_FAILED",
                "IPC_CONNECTION_LOST",
                "WORKER_CRASHED",
                "WORKER_NOT_READY",
            }
            raw_retry_after = exc.details.get("retry_after_seconds", 0.0)
            retry_after = (
                max(0.0, float(raw_retry_after))
                if isinstance(raw_retry_after, (int, float))
                and not isinstance(raw_retry_after, bool)
                else 0.0
            )
            return False, reason, retry_same, retry_after
        except (ProtocolError, OSError, RuntimeError, ValueError):
            return False, "IQOPTION_WEBSOCKET_UNAVAILABLE", True, 0.0
        self._iqoption_balance = balance
        self._iqoption_clock = clock
        self._iqoption_session_invalidated = False
        self._iqoption_auto_trader.on_transport_up()
        if self._transport_supervisor.armed_intent:
            self._iqoption_bot_armed = True
            self._iqoption_bot_reason = (
                "IQOPTION_BOT_ARMED" if clock is not None else "MD_CLOCK_UNTRUSTED"
            )
        return True, "IQOPTION_WEBSOCKET_SESSION_REUSED", False, 0.0

    def safe_stop(
        self,
        *,
        caller: StopReason,
        preserve_operator_intent: bool = False,
    ) -> None:
        assert_safe_stop_caller(caller)
        runtime = self._require_runtime()
        if self._deriv_account_id is not None:
            runtime.stop_new_entries_for(Broker.DERIV, self._deriv_account_id)
        # Launcher shutdown blocks the current process immediately without
        # erasing the durable operator choice. Explicit UI stop commands use
        # the default and persist DISARMED.
        self._stop_iqoption_execution(
            caller,
            "IQOPTION_BOT_DISARMED",
            persist_operator_intent=not preserve_operator_intent,
        )
        self._safe_stop = True
        # Lifecycle READY means the Core/UI control plane is available. Trading
        # authority is represented independently by _safe_stop/HealthGate.
        self._state = CoreServiceState.READY

    def resume(self) -> bool:
        runtime = self._require_runtime()
        trader = self._deriv_auto_trader
        if trader is not None:
            manual_resume = getattr(trader, "manual_resume", None)
            if callable(manual_resume) and not manual_resume():
                self._safe_stop = True
                self._state = CoreServiceState.READY
                return False
            if not callable(manual_resume):
                trader.begin_new_run()
        accepted = (
            runtime.resume_new_entries_for(Broker.DERIV, self._deriv_account_id)
            if self._deriv_account_id is not None
            else runtime.resume_new_entries()
        )
        if not accepted and self._should_reset_demo_session_before_rearm(runtime):
            reset_accepted, _reason = self.reset_digit_test_session()
            if reset_accepted:
                if trader is not None:
                    trader.begin_new_run()
                accepted = (
                    runtime.resume_new_entries_for(Broker.DERIV, self._deriv_account_id)
                    if self._deriv_account_id is not None
                    else runtime.resume_new_entries()
                )
        self._safe_stop = not accepted
        self._state = CoreServiceState.READY
        return accepted

    def _should_reset_demo_session_before_rearm(self, runtime: CoreRuntime) -> bool:
        if self._deriv_transport != "live-demo" or not self._safe_stop:
            return False
        snapshot = runtime.health_gate.get_snapshot()
        active = set(snapshot.active_blockers)
        return bool(active & _DEMO_TEST_SESSION_BLOCKERS)

    def reset_digit_test_session(self) -> tuple[bool, str]:
        """Allow an operator reset only for a disarmed authenticated Demo session."""

        if self._deriv_transport != "live-demo":
            return False, "DERIV_DEMO_REQUIRED"
        runtime = self._require_runtime()
        runtime_safe_stop = bool(getattr(runtime, "safe_stop_active", False))
        if not (self._safe_stop or runtime_safe_stop):
            return False, "SAFE_STOP_REQUIRED"
        accepted, reason = runtime.reset_digit_test_session()
        if accepted and self._deriv_auto_trader is not None:
            self._deriv_auto_trader.reload_runtime_caches()
        if accepted:
            self._safe_stop = True
        return accepted, "DIGIT_TEST_SESSION_RESET" if accepted else reason or "RESET_REJECTED"

    def _update_digit_risk_config(
        self,
        config: DigitRiskConfig,
    ) -> tuple[bool, str | None]:
        """Apply selection config while enforcing Demo-only stress mode."""

        if (
            self._deriv_transport == "live-real"
            and config.selection_mode is StrategySelectionMode.STRESS
        ):
            return False, "DIGIT_STRESS_MODE_REQUIRES_DEMO"
        runtime = self._require_runtime()
        return runtime.update_digit_risk_config(config)

    def _request_ui_shutdown(self) -> None:
        self._ui_shutdown_requested = True

    def connect_deriv_selected_account(self) -> tuple[bool, str]:
        with self._deriv_switch_lock:
            return self._connect_deriv_selected_account_locked()

    def connect_iqoption_selected_account(self, account_mode: str) -> tuple[bool, bool, str]:
        """Start the isolated read-only connector for an explicit IQ Option balance."""

        normalized_mode = account_mode.strip().lower()
        if normalized_mode == "saved":
            try:
                saved_mode = IQOptionCredentialVault(
                    self._profile_dir / "broker_credentials"
                ).configured_account_mode()
            except (OSError, RuntimeError, ValueError):
                return False, False, "IQOPTION_SAVED_LOGIN_UNAVAILABLE"
            if saved_mode is None:
                return False, False, "IQOPTION_CREDENTIALS_NOT_CONFIGURED"
            if saved_mode != "practice":
                # A persisted Real selection never becomes an automatic startup
                # selection. The operator must confirm it in the protected dialog.
                return False, False, "IQOPTION_SAVED_REAL_REQUIRES_CONFIRMATION"
            normalized_mode = saved_mode
        if normalized_mode not in {"practice", "real"}:
            return False, False, "IQOPTION_ACCOUNT_MODE_INVALID"
        if self._state in {CoreServiceState.STOPPING, CoreServiceState.STOPPED}:
            return False, False, "LIFECYCLE_STOPPING"
        # A durable-order recovery can spend several seconds waiting on the
        # external authentication endpoint while holding the broker switch
        # lock.  Do not make a manual UI command wait behind that attempt: the
        # UI must remain responsive and can report the in-progress state.
        if self._iqoption_connecting is not None:
            return False, False, "IQOPTION_CONNECTION_IN_PROGRESS"

        with self._iqoption_switch_lock:
            if (
                self._iqoption is not None
                and not self._iqoption_session_invalidated
                and self._iqoption.health_state is WorkerHealthState.READY
                and self._iqoption_balance is not None
                and (
                    (
                        normalized_mode == "practice"
                        and self._iqoption_balance.account_type == "DEMO"
                    )
                    or (normalized_mode == "real" and self._iqoption_balance.account_type == "REAL")
                )
            ):
                if not self._iqoption_message_budget.try_acquire_operational(
                    time.monotonic()
                ).allowed:
                    return False, False, "IQOPTION_MESSAGE_BUDGET_EXHAUSTED"
                try:
                    # This request reaches _ensure_connected in the worker. Do
                    # not infer external health from a cached Core balance.
                    verified_balance = self._iqoption.client.broker_balance()
                    if verified_balance.account_type != self._iqoption_balance.account_type:
                        raise RuntimeError("IQOPTION_ACCOUNT_MODE_MISMATCH")
                except (WorkerDispatchError, ProtocolError, RuntimeError, OSError, ValueError):
                    self._iqoption_session_invalidated = True
                    self._iqoption_balance = None
                    self._iqoption_clock = None
                    self._iqoption_auto_trader.on_transport_down(
                        "IQOPTION_BROKER_SESSION_UNAVAILABLE"
                    )
                    if self._transport_supervisor.armed_intent:
                        self._iqoption_bot_reason = "TRANSPORT_DOWN"
                else:
                    self._iqoption_balance = verified_balance
                    return (
                        True,
                        True,
                        "IQOPTION_PRACTICE_ALREADY_CONNECTED"
                        if normalized_mode == "practice"
                        else "IQOPTION_REAL_ALREADY_CONNECTED",
                    )
            connection_safety = self._iqoption_connection_safety
            if connection_safety is None:
                return False, False, "IQOPTION_CONNECTION_SAFETY_STATE_INVALID"
            try:
                admission = connection_safety.admit_http_login()
            except IQOptionConnectionSafetyStateError:
                self._iqoption_connection_safety = None
                return False, False, "IQOPTION_CONNECTION_SAFETY_STATE_INVALID"
            if not admission.allowed:
                runtime = self._require_runtime()
                runtime.event_sink.emit(
                    "iqoption_connection_quarantine",
                    reason_code=admission.reason_code,
                    attempts_in_window=admission.attempts_in_window,
                    retry_after_seconds=admission.retry_after_seconds,
                )
                return False, False, admission.reason_code
            runtime = self._require_runtime()
            self._iqoption_auto_trader.on_transport_down("IQOPTION_CONNECTION_CHANGE")
            runtime.detach_iqoption_worker()
            previous = self._iqoption
            self._iqoption = None
            self._iqoption_balance = None
            self._iqoption_clock = None
            if previous is not None:
                previous.shutdown(1.0)

            supervisor = ReadOnlyWorkerSupervisor(
                runtime.health_gate,
                self._iqoption_spec(normalized_mode),
                handshake_timeout=_IQOPTION_WORKER_HANDSHAKE_TIMEOUT_SECONDS,
                response_timeout=_IQOPTION_WORKER_RESPONSE_TIMEOUT_SECONDS,
                heartbeat_timeout=_IQOPTION_WORKER_HEARTBEAT_TIMEOUT_SECONDS,
                disconnect_notifier=self._request_iqoption_recovery_from,
            )
            self._iqoption_connecting = supervisor
            try:
                supervisor.start()
                if self._iqoption_recovery_stop.is_set() or self._state in {
                    CoreServiceState.STOPPING,
                    CoreServiceState.STOPPED,
                }:
                    supervisor.shutdown(0.2)
                    return False, False, "LIFECYCLE_STOPPING"
                expected_connection_mode = (
                    "DEMO_AUTH_FINANCIAL"
                    if normalized_mode == "practice"
                    else "REAL_AUTH_READ_ONLY"
                )
                if supervisor.client.capabilities.connection_mode != expected_connection_mode:
                    raise RuntimeError("IQOPTION_ACCOUNT_MODE_MISMATCH")
                if not self._iqoption_message_budget.try_acquire_operational(
                    time.monotonic()
                ).allowed:
                    raise RuntimeError("IQOPTION_MESSAGE_BUDGET_EXHAUSTED")
                balance = supervisor.client.broker_balance()
                clock: BrokerClockSnapshot | None = None
                try:
                    if self._iqoption_message_budget.try_acquire_operational(
                        time.monotonic()
                    ).allowed:
                        clock = supervisor.client.broker_clock()
                except WorkerDispatchError as exc:
                    if exc.code.value not in IQOPTION_CLOCK_FAILURE_REASONS:
                        raise
                    runtime.health_gate.block_scope(
                        Broker.IQ_OPTION.value,
                        IQOPTION_PRACTICE_ACCOUNT_ID,
                        "MD_CLOCK_UNTRUSTED",
                    )
                if normalized_mode == "practice":
                    runtime.attach_iqoption_worker(
                        supervisor.client,
                        on_order_event=self._iqoption_auto_trader.notify_order_event,
                    )
            except WorkerDispatchError as exc:
                runtime.detach_iqoption_worker()
                supervisor.shutdown(1.0)
                self._record_iqoption_connection_failure(exc.code.value)
                return False, False, exc.code.value
            except ProtocolError as exc:
                runtime.detach_iqoption_worker()
                supervisor.shutdown(1.0)
                self._record_iqoption_connection_failure(exc.code.value)
                return False, False, exc.code.value
            except RuntimeError as exc:
                runtime.detach_iqoption_worker()
                supervisor.shutdown(1.0)
                reason = str(exc).strip()
                if not reason or not all(
                    character.isupper() or character.isdigit() or character == "_"
                    for character in reason
                ):
                    reason = "IQOPTION_CONNECT_FAILED"
                self._record_iqoption_connection_failure(reason)
                return False, False, reason
            except (OSError, ValueError):
                runtime.detach_iqoption_worker()
                supervisor.shutdown(1.0)
                self._record_iqoption_connection_failure("IQOPTION_CONNECT_FAILED")
                return False, False, "IQOPTION_CONNECT_FAILED"
            finally:
                if self._iqoption_connecting is supervisor:
                    self._iqoption_connecting = None

            try:
                connection_safety.record_success()
            except IQOptionConnectionSafetyStateError:
                runtime.detach_iqoption_worker()
                supervisor.shutdown(1.0)
                self._iqoption_connection_safety = None
                return False, False, "IQOPTION_CONNECTION_SAFETY_STATE_INVALID"
            self._iqoption = supervisor
            self._iqoption_balance = balance
            self._iqoption_clock = clock
            self._iqoption_session_invalidated = False
            self._iqoption_auto_trader.on_transport_up()
            if self._transport_supervisor.armed_intent:
                self._iqoption_bot_armed = True
                if runtime.resume_new_entries_for(
                    Broker.IQ_OPTION,
                    IQOPTION_PRACTICE_ACCOUNT_ID,
                ):
                    self._iqoption_bot_reason = "IQOPTION_BOT_ARMED"
                else:
                    self._iqoption_bot_reason = (
                        runtime.health_gate.state_for(
                            Broker.IQ_OPTION.value,
                            IQOPTION_PRACTICE_ACCOUNT_ID,
                        ).reason_code
                        or "IQOPTION_HEALTH_GATE_BLOCKED"
                    )
            else:
                self._iqoption_bot_reason = "IQOPTION_BOT_READY_FOR_CAPABILITY_CHECK"
            if self._iqoption_auto_trader is not None:
                self._iqoption_auto_trader.start()
            return (
                True,
                True,
                "IQOPTION_PRACTICE_CONNECTED"
                if normalized_mode == "practice"
                else "IQOPTION_REAL_READ_ONLY_CONNECTED",
            )

    def _record_iqoption_connection_failure(self, reason_code: str) -> None:
        controller = self._iqoption_connection_safety
        if controller is None:
            return
        try:
            controller.record_failure(reason_code)
        except IQOptionConnectionSafetyStateError:
            self._iqoption_connection_safety = None

    def update_iqoption_risk_config(
        self,
        config: IqOptionRiskConfig,
    ) -> tuple[bool, str | None]:
        """Persist IQ settings only while its independent bot is disarmed."""

        with self._iqoption_switch_lock:
            if self._iqoption_bot_armed:
                return False, "IQOPTION_BOT_MUST_BE_DISARMED"
            if config.symbol != "AUTO" and config.strategy_id != "iqoption-rsi-demo":
                info = self._manifest_catalog.active_strategies.get(config.active_strategy_key)
                if info is None:
                    return False, "NO_CANDIDATE"
                timeframe = TIMEFRAMES.get(info.entry.timeframe)
                if timeframe is None:
                    return False, "TIMEFRAME_UNSUPPORTED"
                if (
                    timeframe != config.timeframe_seconds
                    and timeframe != self._iqoption_risk_config.timeframe_seconds
                ):
                    self._emit_manifest_event(
                        "TIMEFRAME_OVERRIDDEN_BY_MANIFEST",
                        {
                            "strategy_key": info.entry.key,
                            "timeframe": timeframe,
                        },
                    )
                try:
                    config = replace(config, symbol=info.entry.asset, timeframe_seconds=timeframe)
                except ValueError:
                    return False, "IQOPTION_MANIFEST_CONTEXT_UNSUPPORTED"
            try:
                self._iqoption_risk_store.save(config)
            except OSError:
                return False, "IQOPTION_RISK_CONFIG_PERSIST_FAILED"
            self._iqoption_risk_config = config
            self._iqoption_bot_reason = "IQOPTION_RISK_CONFIG_APPLIED"
            return True, None

    def control_iqoption_bot(self, enabled: bool) -> tuple[bool, str]:
        """Control IQ independently and arm when practice capability is satisfied."""

        with self._iqoption_switch_lock:
            if not enabled:
                self._stop_iqoption_execution(
                    StopReason.USER_COMMAND,
                    "IQOPTION_BOT_DISARMED",
                )
                return True, self._iqoption_bot_reason
            supervisor = self._iqoption
            balance = self._iqoption_balance
            if (
                self._iqoption_session_invalidated
                or supervisor is None
                or supervisor.health_state is not WorkerHealthState.READY
            ):
                if balance is not None and balance.account_type.upper() not in {
                    "DEMO",
                    "PRACTICE",
                }:
                    self._iqoption_bot_reason = "IQOPTION_PRACTICE_REQUIRED"
                    return False, self._iqoption_bot_reason
                try:
                    saved_mode = IQOptionCredentialVault(
                        self._profile_dir / "broker_credentials"
                    ).configured_account_mode()
                except (OSError, RuntimeError, ValueError):
                    saved_mode = None
                if saved_mode != "practice":
                    self._iqoption_bot_reason = "IQOPTION_CONNECTION_REQUIRED"
                    return False, self._iqoption_bot_reason
                iq_runtime = self._runtime
                if iq_runtime is None:
                    self._iqoption_bot_reason = "IQOPTION_CORE_NOT_READY"
                    return False, self._iqoption_bot_reason
                global_state = iq_runtime.health_gate.global_state
                if not global_state.is_open:
                    self._iqoption_bot_reason = (
                        global_state.reason_code or "IQOPTION_HEALTH_GATE_BLOCKED"
                    )
                    return False, self._iqoption_bot_reason
                resumed = iq_runtime.resume_new_entries_for(
                    Broker.IQ_OPTION,
                    IQOPTION_PRACTICE_ACCOUNT_ID,
                )
                blocker = iq_runtime.health_gate.state_for(
                    Broker.IQ_OPTION.value,
                    IQOPTION_PRACTICE_ACCOUNT_ID,
                ).reason_code
                transport_blockers = {
                    None,
                    "HG_WORKER_CIRCUIT_OPEN",
                    "HG_WORKER_DISCONNECTED",
                    "HG_WORKER_NOT_READY",
                    "HG_MARKET_DATA_DISCONNECTED",
                    "MD_CLOCK_UNTRUSTED",
                }
                if not resumed and blocker not in transport_blockers:
                    iq_runtime.stop_new_entries_for(
                        Broker.IQ_OPTION,
                        IQOPTION_PRACTICE_ACCOUNT_ID,
                    )
                    self._iqoption_bot_reason = blocker or "IQOPTION_HEALTH_GATE_BLOCKED"
                    return False, self._iqoption_bot_reason
                try:
                    self._iqoption_execution_transport().arm(transport_available=False)
                except (OSError, ValueError):
                    self._iqoption_bot_reason = "IQOPTION_OPERATOR_INTENT_PERSIST_FAILED"
                    return False, self._iqoption_bot_reason
                self._iqoption_bot_armed = True
                self._iqoption_auto_trader.on_transport_down("IQOPTION_CONNECTION_REQUIRED")
                self._iqoption_auto_trader.begin_new_run()
                self._iqoption_auto_trader.start()
                self._iqoption_bot_reason = "TRANSPORT_DOWN"
                iq_runtime.event_sink.emit(
                    "iqoption_operator_intent_armed",
                    reason_code="TRANSPORT_DOWN",
                    execution_state=ExecutionState.ARMED_DEGRADED.value,
                )
                self._request_iqoption_recovery("IQOPTION_OPERATOR_ARMED_DEGRADED")
                return True, "IQOPTION_BOT_ARMED_DEGRADED"
            if balance is None or balance.account_type.upper() not in {"DEMO", "PRACTICE"}:
                self._iqoption_bot_reason = "IQOPTION_PRACTICE_REQUIRED"
                return False, self._iqoption_bot_reason
            capabilities = supervisor.client.capabilities
            capability_ready = all(
                (
                    capabilities.can_submit_orders,
                    capabilities.supports_market_data,
                    capabilities.supports_reconciliation,
                    capabilities.supports_order_events,
                )
            )
            if not capability_ready:
                self._iqoption_bot_reason = "IQOPTION_PRACTICE_TRADING_CAPABILITY_UNAVAILABLE"
                return False, self._iqoption_bot_reason
            iq_runtime = self._runtime
            if iq_runtime is None:
                self._iqoption_bot_reason = "IQOPTION_CORE_NOT_READY"
                return False, self._iqoption_bot_reason
            if not iq_runtime.resume_new_entries_for(
                Broker.IQ_OPTION,
                IQOPTION_PRACTICE_ACCOUNT_ID,
            ):
                self._iqoption_bot_reason = (
                    iq_runtime.health_gate.state_for(
                        Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID
                    ).reason_code
                    or "IQOPTION_HEALTH_GATE_BLOCKED"
                )
                return False, self._iqoption_bot_reason
            self._iqoption_bot_armed = True
            self._iqoption_execution_transport().arm()
            if hasattr(self, "_iqoption_auto_trader") and self._iqoption_auto_trader is not None:
                self._iqoption_auto_trader.begin_new_run()
                self._iqoption_auto_trader.start()
            self._iqoption_bot_reason = "IQOPTION_BOT_ARMED"
            return True, self._iqoption_bot_reason

    def _stop_iqoption_execution(
        self,
        caller: StopReason,
        reason_code: str,
        *,
        persist_operator_intent: bool = True,
    ) -> None:
        """Central stop boundary; transport code is not an authorized caller."""

        assert_safe_stop_caller(caller)
        self._iqoption_execution_transport().safe_stop(
            caller=caller,
            persist_intent=persist_operator_intent,
        )
        self._iqoption_bot_armed = False
        self._iqoption_bot_reason = reason_code
        runtime = self._runtime
        if runtime is not None:
            runtime.stop_new_entries_for(
                Broker.IQ_OPTION,
                IQOPTION_PRACTICE_ACCOUNT_ID,
            )
        trader = getattr(self, "_iqoption_auto_trader", None)
        if trader is not None:
            trader.invalidate_entry_authority(reason_code)

    def _iqoption_execution_transport(self) -> TransportSupervisor:
        controller = getattr(self, "_transport_supervisor", None)
        if controller is None:
            controller = TransportSupervisor(
                initially_armed=bool(getattr(self, "_iqoption_bot_armed", False))
            )
            self._transport_supervisor = controller
        return controller

    def _connect_deriv_selected_account_locked(self) -> tuple[bool, str]:
        """Replace the public worker with the explicitly selected authenticated account."""
        if self._state in {CoreServiceState.STOPPING, CoreServiceState.STOPPED}:
            return False, "LIFECYCLE_STOPPING"
        if "deriv_read_only" not in self._workers:
            return False, "DERIV_WORKER_DISABLED"
        try:
            credentials = DerivCredentialVault(self._profile_dir / "broker_credentials").load()
        except (OSError, RuntimeError, ValueError):
            return False, "DERIV_CREDENTIALS_INVALID"
        selected_type = None if credentials is None else credentials.account_type
        if selected_type not in {"demo", "real"}:
            return False, "DERIV_CREDENTIALS_REQUIRED"
        selected_transport = "live-real" if selected_type == "real" else "live-demo"
        if (
            self._deriv_transport == selected_transport
            and self._deriv is not None
            and self._deriv.health_state is WorkerHealthState.READY
            and (selected_type == "real" or self._deriv_auto_trader is not None)
        ):
            return True, "DERIV_ACCOUNT_ALREADY_CONNECTED"
        runtime = self._require_runtime()
        if self._deriv_transport in {"live-demo", "live-real"} and self._has_open_deriv_orders():
            return False, "DERIV_ACCOUNT_SWITCH_BLOCKED_OPEN_ORDERS"
        if self._deriv_account_id is not None:
            runtime.stop_new_entries_for(Broker.DERIV, self._deriv_account_id)
        self._safe_stop = True
        self._state = CoreServiceState.READY
        self._stop_deriv_telemetry()
        self._stop_deriv_financial_runtime(runtime)
        current = self._deriv
        self._deriv = None
        if current is not None:
            current.shutdown(3.0)
        previous_transport = self._deriv_transport
        self._deriv_transport = selected_transport
        last_error: Exception | None = None
        for retry_delay in (0.0, 1.0, 2.0):
            if retry_delay and self._deriv_recovery_stop.wait(retry_delay):
                last_error = RuntimeError("LIFECYCLE_STOPPING")
                break
            replacement = ReadOnlyWorkerSupervisor(
                runtime.health_gate,
                self._deriv_spec(),
                handshake_timeout=25.0,
                response_timeout=12.0,
                heartbeat_timeout=10.0,
            )
            try:
                self._deriv = replacement
                replacement.start()
                self._start_deriv_telemetry(runtime, replacement)
                self._activate_deriv_financial_runtime(runtime, replacement)
                if hasattr(replacement, "health_state") and not self._deriv_activation_ready():
                    raise RuntimeError("DERIV_ACTIVATION_NOT_READY")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                self._stop_deriv_financial_runtime(runtime)
                self._stop_deriv_telemetry()
                self._deriv = None
                replacement.shutdown(1.0)
        if last_error is not None:
            failure_reason = self._deriv_connect_failure_reason(last_error)
            self._deriv_transport = previous_transport
            try:
                fallback = ReadOnlyWorkerSupervisor(
                    runtime.health_gate,
                    self._deriv_spec(),
                    heartbeat_timeout=(
                        10.0 if self._deriv_transport in {"live-demo", "live-real"} else 1.0
                    ),
                )
                fallback.start()
                self._deriv = fallback
                self._start_deriv_telemetry(runtime, fallback)
                self._activate_deriv_financial_runtime(runtime, fallback)
            except Exception:
                self._state = CoreServiceState.READY
            return False, failure_reason
        self._restart_counts["DERIV_WORKER"] += 1
        return True, "DERIV_REAL_CONNECTED" if selected_type == "real" else "DERIV_DEMO_CONNECTED"

    @staticmethod
    def _deriv_connect_failure_reason(error: Exception) -> str:
        explicit = str(getattr(error, "reason_code", ""))
        if explicit in {
            "DERIV_AUTH_FAILED",
            "DERIV_NETWORK_ERROR",
            "DERIV_DEMO_ACCOUNT_NOT_FOUND",
            "DERIV_ACCOUNT_TYPE_MISMATCH",
            "DERIV_SCHEMA_INCOMPATIBLE",
        }:
            return explicit
        error_text = str(error)
        if "TIMEOUT" in error_text.upper() or "did not connect" in error_text:
            return "DERIV_CONNECTION_TIMEOUT"
        if error_text == "LIFECYCLE_STOPPING":
            return error_text
        return "DERIV_ACCOUNT_CONNECT_FAILED"

    def _emit_manifest_event(self, event: str, fields: dict[str, object]) -> None:
        if self._runtime is not None:
            safe_fields: dict[str, str | int | bool | None] = {
                key: value
                for key, value in fields.items()
                if key != "reason_code" and (isinstance(value, (str, int, bool)) or value is None)
            }
            self._runtime.event_sink.emit(
                event, reason_code=str(fields.get("reason_code", event)), **safe_fields
            )

    def _on_manifest_applied(self, manifest: ManifestRecord) -> None:
        """Finish a remote swap without granting or preserving entry authority."""
        if self._iqoption_bot_armed:
            self._stop_iqoption_execution(
                StopReason.STRATEGY_GATE,
                "IQOPTION_BOT_DISARMED_AFTER_MANIFEST_CHANGE",
            )
        monitor = self._live_monitor
        if monitor is not None:
            monitor.on_manifest_applied(manifest)
        self._emit_manifest_event(
            "manifest_runtime_applied",
            {
                "manifest_version": manifest.manifest_version,
                "iqoption_bot_armed": self._iqoption_bot_armed,
            },
        )

    def _request_deriv_recovery(self, _reason_code: str) -> None:
        if (
            self._deriv_transport not in {"live-demo", "live-real"}
            or self._state in {CoreServiceState.STOPPING, CoreServiceState.STOPPED}
            or self._deriv_recovery_stop.is_set()
        ):
            return
        if self._state is CoreServiceState.STARTING:
            self._pending_deriv_recovery_reason = _reason_code
            self._safe_stop = True
            return
        runtime = self._runtime
        if runtime is not None:
            sink = getattr(runtime, "event_sink", None)
            if sink is not None:
                sink.emit(
                    "deriv_recovery_requested",
                    reason_code=_reason_code,
                    transport=self._deriv_transport,
                    generation=self._deriv_generation,
                )
            if self._deriv_account_id is not None:
                runtime.stop_new_entries_for(Broker.DERIV, self._deriv_account_id)
        self._safe_stop = True
        self._state = CoreServiceState.READY
        with self._deriv_switch_lock:
            current = self._deriv_recovery_thread
            if current is not None and current.is_alive():
                return
            thread = threading.Thread(
                target=self._deriv_recovery_loop,
                name="deriv-authenticated-recovery",
                daemon=True,
            )
            self._deriv_recovery_thread = thread
            thread.start()

    def _deriv_recovery_loop(self) -> None:
        delays = (0.0, 1.0, 2.0, 5.0, 10.0, 30.0)
        attempt = 0
        while not self._deriv_recovery_stop.is_set():
            delay = delays[min(attempt, len(delays) - 1)]
            if self._deriv_recovery_stop.wait(delay):
                return
            runtime = self._runtime
            if runtime is not None:
                sink = getattr(runtime, "event_sink", None)
                if sink is not None:
                    sink.emit(
                        "deriv_recovery_attempt",
                        attempt=attempt + 1,
                        delay_ms=int(delay * 1000),
                        transport=self._deriv_transport,
                        generation=self._deriv_generation,
                    )
            if self._recover_deriv_connection_once():
                return
            attempt += 1

    def _recover_deriv_connection_once(self) -> bool:
        with self._deriv_switch_lock:
            if self._deriv_transport not in {"live-demo", "live-real"} or self._state in {
                CoreServiceState.STOPPING,
                CoreServiceState.STOPPED,
            }:
                return False
            runtime = self._runtime
            if runtime is None:
                return False
            self._stop_deriv_telemetry()
            self._stop_deriv_financial_runtime(runtime)
            current = self._deriv
            self._deriv = None
            if current is not None:
                current.shutdown(3.0)
            replacement: ReadOnlyWorkerSupervisor | None = None
            try:
                replacement = ReadOnlyWorkerSupervisor(
                    runtime.health_gate,
                    self._deriv_spec(),
                    handshake_timeout=45.0,
                    response_timeout=12.0,
                    heartbeat_timeout=10.0,
                )
                self._deriv = replacement
                replacement.start()
                self._start_deriv_telemetry(runtime, replacement)
                self._activate_deriv_financial_runtime(runtime, replacement)
                if hasattr(replacement, "health_state") and not self._deriv_activation_ready():
                    raise RuntimeError("DERIV_ACTIVATION_NOT_READY")
            except Exception:
                if replacement is not None:
                    replacement.shutdown(1.0)
                self._deriv = None
                self._state = CoreServiceState.READY
                sink = getattr(runtime, "event_sink", None)
                if sink is not None:
                    sink.emit(
                        "deriv_recovery_failed",
                        reason_code="DERIV_RECOVERY_ATTEMPT_FAILED",
                        transport=self._deriv_transport,
                        generation=self._deriv_generation,
                    )
                return False
            self._restart_counts["DERIV_WORKER"] += 1
            # Transport recovery never grants trading authority. A fresh explicit
            # operator action is required and will invalidate all pre-drop signals.
            self._safe_stop = True
            self._state = CoreServiceState.READY
            sink = getattr(runtime, "event_sink", None)
            if sink is not None:
                sink.emit(
                    "deriv_recovery_succeeded",
                    reason_code="OPERATOR_REARM_REQUIRED",
                    transport=self._deriv_transport,
                    generation=self._deriv_generation,
                )
            return True

    def connect_deriv_demo(self) -> tuple[bool, str]:
        return self.connect_deriv_selected_account()

    def _has_open_deriv_orders(self) -> bool:
        runtime = self._require_runtime()
        return any(
            str(row.get("broker")) == Broker.DERIV.value
            for row in runtime.reader.list_nonterminal_orders()
        )

    def drain(self, timeout: float) -> tuple[bool, int]:
        runtime = self._require_runtime()
        drained = runtime.drain_financial_events(timeout)
        return drained, runtime.pending_financial_event_count

    def shutdown_workers(self, grace_seconds: float) -> bool:
        if self._workers_stopped:
            return True
        self._deriv_recovery_stop.set()
        self._iqoption_recovery_stop.set()
        self._manifest_refresh.stop()
        self._state = CoreServiceState.STOPPING
        connecting_iqoption = self._iqoption_connecting
        self._iqoption_connecting = None
        if connecting_iqoption is not None:
            connecting_iqoption.shutdown(min(0.5, grace_seconds))
        self._stop_deriv_telemetry()
        runtime = self._require_runtime()
        self._stop_deriv_financial_runtime(runtime)
        if self._deriv is not None:
            self._deriv.shutdown(grace_seconds)
            self._deriv = None
        if self._iqoption is not None:
            if hasattr(self, "_iqoption_auto_trader") and self._iqoption_auto_trader is not None:
                self._iqoption_auto_trader.stop()
            runtime.detach_iqoption_worker()
            self._iqoption.shutdown(grace_seconds)
            self._iqoption = None
            self._iqoption_balance = None
            self._iqoption_clock = None
        recovery = self._deriv_recovery_thread
        self._deriv_recovery_thread = None
        if recovery is not None and recovery is not threading.current_thread():
            recovery.join(timeout=grace_seconds)
        iqoption_recovery = self._iqoption_startup_recovery_thread
        self._iqoption_startup_recovery_thread = None
        if iqoption_recovery is not None and iqoption_recovery is not threading.current_thread():
            iqoption_recovery.join(timeout=min(0.5, grace_seconds))
        drained = runtime.shutdown_workers(grace_seconds)
        self._workers_stopped = True
        return drained

    def shutdown_auth(self, grace_seconds: float) -> None:
        if self._auth_stopped:
            return
        self._auth.shutdown(grace_seconds)
        self._auth_stopped = True

    def shutdown_core(self) -> None:
        if self._state is CoreServiceState.STOPPED:
            return
        self._state = CoreServiceState.STOPPING
        self._manifest_refresh.stop()
        if self._live_monitor is not None:
            self._live_monitor.stop()
        if self._outcomes_uploader is not None:
            self._outcomes_uploader.stop()
            self._outcomes_uploader = None
        ui_service = self._ui_service
        self._ui_service = None
        if ui_service is not None:
            ui_service.stop()
        runtime = self._runtime
        if runtime is not None:
            runtime.shutdown()
            self._runtime = None
        self._state = CoreServiceState.STOPPED

    def emergency_shutdown(self) -> None:
        self._safe_stop = True
        try:
            if self._runtime is not None:
                self._runtime.stop_new_entries()
                self.shutdown_workers(0.5)
        finally:
            try:
                self.shutdown_auth(0.5)
            finally:
                self.shutdown_core()

    def restart_component(self, role: str) -> tuple[bool, str]:
        if self._state in {CoreServiceState.STOPPING, CoreServiceState.STOPPED}:
            return False, "LIFECYCLE_STOPPING"
        if role == "AUTH_AGENT":
            self._auth.restart()
            self._restart_counts[role] += 1
            return True, "RESTART_COMPLETED"
        if role == "DERIV_WORKER" and "deriv_read_only" in self._workers:
            runtime = self._require_runtime()
            if self._deriv_account_id is not None:
                runtime.stop_new_entries_for(Broker.DERIV, self._deriv_account_id)
            self._safe_stop = True
            self._state = CoreServiceState.READY
            self._stop_deriv_telemetry()
            self._stop_deriv_financial_runtime(runtime)
            if self._deriv is None:
                self._deriv = ReadOnlyWorkerSupervisor(
                    runtime.health_gate,
                    self._deriv_spec(),
                    handshake_timeout=(
                        45.0 if self._deriv_transport in {"live-demo", "live-real"} else 5.0
                    ),
                    response_timeout=(
                        12.0 if self._deriv_transport in {"live-demo", "live-real"} else 2.0
                    ),
                    heartbeat_timeout=(
                        10.0 if self._deriv_transport in {"live-demo", "live-real"} else 1.0
                    ),
                )
                self._deriv.start()
            else:
                self._deriv.restart()
            self._start_deriv_telemetry(runtime, self._deriv)
            self._activate_deriv_financial_runtime(runtime, self._deriv)
            self._restart_counts[role] += 1
            return True, "RESTART_COMPLETED"
        return False, "RESTART_NOT_PERMITTED"

    def process_statuses(self) -> tuple[LifecycleProcessStatus, ...]:
        runtime = self._runtime
        auth_process = self._auth.process
        simulated = None if runtime is None else runtime.worker_supervisor
        simulated_process = None if simulated is None else simulated.process
        deriv_process = None if self._deriv is None else self._deriv.process
        statuses: list[LifecycleProcessStatus] = [
            self._status(
                "AUTH_AGENT",
                auth_process,
                self._auth.health_state.value,
                self._restart_counts["AUTH_AGENT"],
            ),
            LifecycleProcessStatus(
                role="CORE",
                pid=os.getpid(),
                is_alive=True,
                exit_code=None,
                state=self._state.value,
                restarts_count=0,
            ),
            self._status(
                "SIMULATED_WORKER",
                simulated_process,
                (
                    WorkerHealthState.STOPPED.value
                    if simulated is None
                    else simulated.health_state.value
                ),
                0,
            ),
        ]
        if "deriv_read_only" in self._workers or self._deriv is not None:
            statuses.append(
                self._status(
                    "DERIV_WORKER",
                    deriv_process,
                    WorkerHealthState.STOPPED.value
                    if self._deriv is None
                    else self._deriv.health_state.value,
                    self._restart_counts["DERIV_WORKER"],
                )
            )
        if "iqoption" in self._workers or self._iqoption is not None:
            statuses.append(
                self._status(
                    "IQOPTION_WORKER",
                    None if self._iqoption is None else self._iqoption.process,
                    (
                        WorkerHealthState.STOPPED.value
                        if self._iqoption is None
                        else self._iqoption.health_state.value
                    ),
                    0,
                )
            )
        return tuple(statuses)

    @staticmethod
    def _status(
        role: str,
        process: object | None,
        state: str,
        restarts: int,
    ) -> LifecycleProcessStatus:
        if process is None:
            return LifecycleProcessStatus(role, None, False, None, state, restarts)
        pid = getattr(process, "pid", None)
        poll = getattr(process, "poll", None)
        if type(pid) is not int or not callable(poll):
            raise RuntimeError("managed process handle is invalid")
        exit_code = poll()
        return LifecycleProcessStatus(role, pid, exit_code is None, exit_code, state, restarts)

    def _require_runtime(self) -> CoreRuntime:
        if self._runtime is None:
            raise RuntimeError("CORE_RUNTIME_UNAVAILABLE")
        return self._runtime

    def _deriv_spec(self) -> ReadOnlyWorkerSpec:
        extra_arguments: tuple[str, ...] = ("--deriv-transport", self._deriv_transport)
        if self._deriv_transport in {"live-demo", "live-real"}:
            extra_arguments = (
                *extra_arguments,
                "--credential-vault-dir",
                str(self._profile_dir / "broker_credentials"),
            )
        return ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
            extra_arguments=extra_arguments,
            allow_demo_financial_submission=self._deriv_transport == "live-demo",
            allow_real_financial_submission=False,
        )

    def _iqoption_spec(self, account_mode: str) -> ReadOnlyWorkerSpec:
        return ReadOnlyWorkerSpec(
            module="apps.iqoption_connection_worker",
            role=EndpointRole.IQOPTION_WORKER,
            broker="IQOPTION",
            extra_arguments=(
                "--vault-dir",
                str(self._profile_dir / "broker_credentials"),
                "--account-mode",
                account_mode,
            ),
            allow_demo_financial_submission=account_mode == "practice",
            allow_real_financial_submission=False,
        )

    def _start_deriv_telemetry(
        self,
        runtime: CoreRuntime,
        supervisor: ReadOnlyWorkerSupervisor,
    ) -> None:
        self._deriv_generation += 1
        generation = self._deriv_generation
        source = {
            "fake-public": DerivTelemetrySource.FAKE_SIMULATED,
            "fake-demo": DerivTelemetrySource.FAKE_SIMULATED,
            "live-public": DerivTelemetrySource.PUBLIC_LIVE,
            "live-demo": DerivTelemetrySource.DEMO_LIVE,
            "live-real": DerivTelemetrySource.REAL_LIVE,
        }[self._deriv_transport]
        monitor = DerivTelemetryMonitor(
            supervisor,
            runtime.health_gate,
            source,
            symbol_provider=lambda: runtime.risk_ledger.digit_config.selected_symbol,
            disconnect_notifier=self._request_deriv_recovery,
            reconciliation_notifier=lambda _reason: runtime.reconcile_deriv_worker(
                supervisor.client
            ),
            generation_is_current=lambda: generation == self._deriv_generation,
        )
        self._deriv_telemetry = monitor
        monitor.start()

    def _activate_deriv_financial_runtime(
        self,
        runtime: CoreRuntime,
        supervisor: ReadOnlyWorkerSupervisor,
    ) -> None:
        if self._deriv_transport == "live-real":
            runtime.reconcile_deriv_worker(supervisor.client)
            return
        if self._deriv_transport != "live-demo":
            return
        credentials = DerivCredentialVault(self._profile_dir / "broker_credentials").load()
        if credentials is None or credentials.account_type != "demo":
            raise RuntimeError("DERIV_DEMO_CREDENTIALS_REQUIRED")
        self._deriv_account_id = credentials.account_id
        runtime.stop_new_entries_for(Broker.DERIV, credentials.account_id)
        telemetry = self._deriv_telemetry
        if telemetry is None:
            raise RuntimeError("DERIV_TELEMETRY_UNAVAILABLE")

        def emit_budget_event(name: str, fields: dict[str, object]) -> None:
            safe_fields: dict[str, str | int | bool | None] = {}
            for key, value in fields.items():
                if isinstance(value, (str, int, bool)) or value is None:
                    safe_fields[key] = value
                else:
                    safe_fields[key] = str(value)
            raw_reason = safe_fields.pop("reason_code", None)
            reason_code = raw_reason if isinstance(raw_reason, str) or raw_reason is None else None
            runtime.event_sink.emit(name, reason_code=reason_code, **safe_fields)

        proposal_cache = PayoutRoutedDiffersProposalCache(
            event_sink=emit_budget_event,
            symbol_provider=lambda: runtime.risk_ledger.digit_config.selected_symbol,
        )
        proposal_feeder = PayoutRoutedDiffersQuoteFeeder(
            proposal_cache,
            supervisor.client.quote_digit_contract_details,
            enabled=lambda: (
                runtime.risk_ledger.digit_config.selection_mode is StrategySelectionMode.STRESS
                or (
                    runtime.risk_ledger.digit_config.selection_mode is StrategySelectionMode.MULTI
                    and PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
                    in runtime.risk_ledger.digit_config.enabled_strategy_ids
                )
                or (
                    runtime.risk_ledger.digit_config.selection_mode is StrategySelectionMode.SINGLE
                    and runtime.risk_ledger.digit_config.active_strategy_id
                    == PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
                )
            ),
        )
        proposal_feeder.start()
        self._payout_differs_feeder = proposal_feeder
        trader = DerivDigitAutoTrader(
            runtime,
            credentials.account_id,
            lambda: None if self._deriv_telemetry is None else self._deriv_telemetry.snapshot,
            operator_armed=lambda: not self._safe_stop,
            quote_provider=supervisor.client.quote_digit_contract,
            proposal_cache=proposal_cache,
            arbitration_notifier=telemetry.record_arbitration,
        )
        runtime.attach_deriv_worker(
            supervisor.client,
            on_order_event=trader.notify_order_event,
            on_reconciliation_completed=trader.reload_runtime_caches,
        )
        telemetry.set_tick_notifier(trader.notify_tick)
        trader.start()
        self._deriv_auto_trader = trader

    def _stop_deriv_financial_runtime(self, runtime: CoreRuntime) -> None:
        proposal_feeder = self._payout_differs_feeder
        self._payout_differs_feeder = None
        if proposal_feeder is not None:
            proposal_feeder.stop()
        trader = self._deriv_auto_trader
        self._deriv_auto_trader = None
        if trader is not None:
            trader.stop()
        telemetry = self._deriv_telemetry
        if telemetry is not None:
            telemetry.set_tick_notifier(None)
        runtime.detach_deriv_worker()

    def _stop_deriv_telemetry(self) -> None:
        """Invalidate an old generation before waiting for its thread to stop."""

        self._deriv_generation += 1
        telemetry = self._deriv_telemetry
        self._deriv_telemetry = None
        if telemetry is not None:
            telemetry.stop()

    def _deriv_activation_ready(self) -> bool:
        supervisor = self._deriv
        telemetry = self._deriv_telemetry
        if (
            supervisor is None
            or supervisor.health_state is not WorkerHealthState.READY
            or telemetry is None
            or not telemetry.snapshot.connected
        ):
            return False
        return self._deriv_transport != "live-demo" or self._deriv_auto_trader is not None
