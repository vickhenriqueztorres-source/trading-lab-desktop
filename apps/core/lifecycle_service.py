from __future__ import annotations

import os
import sys
import threading
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
from apps.core.iqoption_auto_trader import IQOPTION_PRACTICE_ACCOUNT_ID, IqOptionAutoTrader
from apps.core.iqoption_candidates import TIMEFRAMES
from apps.core.iqoption_connection_safety import (
    IQOPTION_MAX_AUTOMATED_RECOVERY_ATTEMPTS,
    IQOptionConnectionSafetyController,
    IQOptionConnectionSafetyStateError,
    IQOptionConnectionSafetyStore,
)
from apps.core.iqoption_risk_config import IqOptionRiskConfig, IqOptionRiskConfigStore
from apps.core.live_monitor import LiveMonitor
from apps.core.manifest_catalog import DynamicManifestCatalog
from apps.core.manifest_client import DEFAULT_PARITY_SHA256, evaluate_manifest_bytes
from apps.core.manifest_keys import PROD_PUBLIC_KEYS
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
_IQOPTION_WORKER_HEARTBEAT_TIMEOUT_SECONDS = 10.0
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
        self._live_monitor: LiveMonitor | None = None
        self._load_local_manifest_catalog()
        self._iqoption_bot_armed = False
        self._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
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
        self._deriv_recovery_thread: threading.Thread | None = None
        self._iqoption_startup_recovery_thread: threading.Thread | None = None
        self._deriv_generation = 0
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
            self._live_monitor = LiveMonitor(
                self._manifest_catalog, writer=runtime.writer, event_sink=runtime.event_sink
            )
            self._live_monitor.start()
            runtime.iqoption_entry_validator = self._iqoption_auto_trader.validate_runtime_entry
            runtime.iqoption_execution_lock = self._manifest_catalog.execution_lock
            runtime.iqoption_order_registered = self._manifest_catalog.notify_order_opened
            # Automated entries always start disarmed. The user must press Ligar Bot.
            runtime.stop_new_entries()
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
                        None if self._iqoption is None else self._iqoption.health_state
                    ),
                    iqoption_balance=lambda: (
                        self._iqoption_auto_trader.latest_balance
                        if self._iqoption_auto_trader is not None
                        and self._iqoption_auto_trader.latest_balance is not None
                        else self._iqoption_balance
                    ),
                    iqoption_clock=lambda: (
                        self._iqoption_auto_trader.latest_clock
                        if self._iqoption_auto_trader is not None
                        and self._iqoption_auto_trader.latest_clock is not None
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
                )
                ui_service = CoreUiProjectionService(
                    self._ui_session_token,
                    projection.snapshot,
                    self.safe_stop,
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
        candidates = [
            self._profile_dir / "cache" / "manifest.json",
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
                    data, reason = evaluate_manifest_bytes(
                        path.read_bytes(),
                        PROD_PUBLIC_KEYS,
                        expected_primitives_version="1.0.0",
                        expected_parity_sha256=DEFAULT_PARITY_SHA256,
                    )
                    if data is None:
                        self._manifest_load_reason = reason
                        self._emit_manifest_event("manifest_rejected", {"reason_code": reason})
                        continue
                    self._manifest_catalog.apply_manifest(data)
                    self._manifest_load_reason = "MANIFEST_ACCEPTED"
                    return
                except Exception:
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
        """Recover a durable Practice order without depending on UI startup timing."""

        if not has_iqoption_recovery:
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
        current = self._iqoption_startup_recovery_thread
        if current is not None and current.is_alive():
            return

        def recover() -> None:
            terminal_reasons = {
                "IQOPTION_AUTH_FAILED",
                "IQOPTION_2FA_REQUIRED",
                "IQOPTION_RATE_LIMITED",
                "IQOPTION_CONNECTION_QUARANTINED",
                "IQOPTION_CONNECTION_SAFETY_STATE_INVALID",
                "IQOPTION_CREDENTIALS_NOT_CONFIGURED",
                "IQOPTION_SAVED_LOGIN_UNAVAILABLE",
                "IQOPTION_SAVED_REAL_REQUIRES_CONFIRMATION",
            }
            attempt = 0
            while (
                attempt < IQOPTION_MAX_AUTOMATED_RECOVERY_ATTEMPTS
                and not self._iqoption_recovery_stop.is_set()
            ):
                delay = _IQOPTION_RECOVERY_DELAYS_SECONDS[
                    min(attempt, len(_IQOPTION_RECOVERY_DELAYS_SECONDS) - 1)
                ]
                attempt += 1
                if self._iqoption_recovery_stop.wait(delay):
                    return
                runtime = self._runtime
                if runtime is None or self._state in {
                    CoreServiceState.STOPPING,
                    CoreServiceState.STOPPED,
                }:
                    return
                runtime.event_sink.emit(
                    "iqoption_startup_recovery_attempt",
                    attempt=attempt,
                    delay_ms=int(delay * 1000),
                )
                accepted, connected, reason = self.connect_iqoption_selected_account("saved")
                if accepted and connected:
                    runtime.event_sink.emit(
                        "iqoption_startup_recovery_connected",
                        reason_code=reason,
                    )
                    return
                runtime.event_sink.emit(
                    "iqoption_startup_recovery_failed",
                    reason_code=reason,
                    attempt=attempt,
                )
                if reason in terminal_reasons:
                    return
            runtime = self._runtime
            if runtime is not None and not self._iqoption_recovery_stop.is_set():
                runtime.event_sink.emit(
                    "iqoption_startup_recovery_exhausted",
                    reason_code="IQOPTION_AUTOMATED_RECOVERY_LIMIT_REACHED",
                    attempts=attempt,
                )

        thread = threading.Thread(
            target=recover,
            name="iqoption-startup-recovery",
            daemon=True,
        )
        self._iqoption_startup_recovery_thread = thread
        thread.start()

    def safe_stop(self) -> None:
        runtime = self._require_runtime()
        stop_entries = getattr(runtime, "stop_new_entries", None)
        if callable(stop_entries) and not self._iqoption_bot_armed:
            stop_entries()
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
        accepted = runtime.resume_new_entries()
        if not accepted and self._should_reset_demo_session_before_rearm(runtime):
            reset_accepted, _reason = self.reset_digit_test_session()
            if reset_accepted:
                if trader is not None:
                    trader.begin_new_run()
                accepted = runtime.resume_new_entries()
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
            self._iqoption_bot_armed = False
            self._iqoption_bot_reason = "IQOPTION_BOT_DISARMED_AFTER_CONNECTION_CHANGE"
            runtime = self._require_runtime()
            self._iqoption_auto_trader.stop()
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
                balance = supervisor.client.broker_balance()
                clock: BrokerClockSnapshot | None = None
                try:
                    clock = supervisor.client.broker_clock()
                except WorkerDispatchError as exc:
                    if exc.code is not ProtocolErrorCode.IQOPTION_CLOCK_UNAVAILABLE:
                        raise
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
            except (OSError, RuntimeError, ValueError):
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
                self._iqoption_bot_armed = False
                runtime = self._require_runtime()
                if self._safe_stop:
                    runtime.stop_new_entries()
                self._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
                return True, self._iqoption_bot_reason
            supervisor = self._iqoption
            balance = self._iqoption_balance
            if supervisor is None or supervisor.health_state is not WorkerHealthState.READY:
                self._iqoption_bot_reason = "IQOPTION_CONNECTION_REQUIRED"
                return False, self._iqoption_bot_reason
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
            if hasattr(self, "_iqoption_auto_trader") and self._iqoption_auto_trader is not None:
                self._iqoption_auto_trader.begin_new_run()
                self._iqoption_auto_trader.start()
            self._iqoption_bot_reason = "IQOPTION_BOT_ARMED"
            return True, self._iqoption_bot_reason

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
        stop_entries = getattr(runtime, "stop_new_entries", None)
        if callable(stop_entries):
            stop_entries()
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
            stop_entries = getattr(runtime, "stop_new_entries", None)
            if callable(stop_entries):
                stop_entries()
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
        if self._live_monitor is not None:
            self._live_monitor.stop()
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
            runtime.stop_new_entries()
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
