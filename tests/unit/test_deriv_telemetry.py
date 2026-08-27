from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from apps.core.deriv_telemetry import DerivTelemetryMonitor, DerivTelemetrySource
from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.worker_client import SocketWorkerClient
from packages.domain.market import (
    BrokerClockSnapshot,
    ContractMetadata,
    MarketSymbol,
    MarketTick,
)
from packages.domain.models import Broker
from packages.strategies.deriv_digits import DigitAssetShadowState


class _Capabilities:
    connection_mode = "PUBLIC_READ_ONLY"


class _ClockClient:
    capabilities = _Capabilities()

    def __init__(self, clock: BrokerClockSnapshot) -> None:
        self.clock = clock

    def broker_clock(self) -> BrokerClockSnapshot:
        return self.clock


class _Supervisor:
    def __init__(self, client: _ClockClient) -> None:
        self.client = client


@pytest.mark.parametrize(
    "round_trip,offset",
    [(1.001, Decimal("0")), (0.010, Decimal("2.001"))],
)
def test_untrusted_deriv_clock_blocks_and_proven_recovery_clears_gate(
    round_trip: float,
    offset: Decimal,
) -> None:
    observed = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    client = _ClockClient(BrokerClockSnapshot(1_700_000_100, observed, round_trip, offset))
    supervisor = cast(ReadOnlyWorkerSupervisor, _Supervisor(client))
    gate = HealthGate()
    monitor = DerivTelemetryMonitor(
        supervisor,
        gate,
        DerivTelemetrySource.PUBLIC_LIVE,
    )

    blocked = monitor.probe_once()
    assert blocked.reason_code == "MD_CLOCK_UNTRUSTED"
    assert gate.contains("MD_CLOCK_UNTRUSTED")

    client.clock = BrokerClockSnapshot(1_700_000_100, observed, 0.010, Decimal("0.100"))
    recovered = monitor.probe_once()
    assert recovered.reason_code is None
    assert not gate.contains("MD_CLOCK_UNTRUSTED")


def test_authenticated_telemetry_failure_requests_supervised_reauthentication() -> None:
    class FailingClient:
        capabilities = _Capabilities()

        def broker_clock(self) -> BrokerClockSnapshot:
            raise RuntimeError("socket unavailable")

    reasons: list[str] = []
    supervisor = cast(ReadOnlyWorkerSupervisor, _Supervisor(FailingClient()))  # type: ignore[arg-type]
    monitor = DerivTelemetryMonitor(
        supervisor,
        HealthGate(),
        DerivTelemetrySource.DEMO_LIVE,
        disconnect_notifier=reasons.append,
    )

    snapshot = monitor.probe_once()

    assert snapshot.connected is False
    assert reasons == ["DERIV_TELEMETRY_UNAVAILABLE"]


def test_retired_telemetry_generation_cannot_reblock_or_clear_current_health() -> None:
    class ForbiddenClient:
        @property
        def capabilities(self) -> object:
            raise AssertionError("retired generation must not access its client")

        def broker_clock(self) -> BrokerClockSnapshot:
            raise AssertionError("retired generation must not probe")

    gate = HealthGate()
    gate.block_scope("DERIV", "market-data", "MD_CLOCK_UNTRUSTED")
    reasons: list[str] = []
    monitor = DerivTelemetryMonitor(
        cast(ReadOnlyWorkerSupervisor, _Supervisor(ForbiddenClient())),  # type: ignore[arg-type]
        gate,
        DerivTelemetrySource.DEMO_LIVE,
        disconnect_notifier=reasons.append,
        generation_is_current=lambda: False,
    )

    assert monitor.probe_once().reason_code == "NOT_PROBED"
    monitor._notify_disconnect("STALE_DISCONNECT")
    assert gate.contains("MD_CLOCK_UNTRUSTED")
    assert not gate.contains("HG_MARKET_DATA_DISCONNECTED")
    assert reasons == []


def test_tick_warmup_history_is_loaded_in_bounded_ipc_pages() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    available = tuple(
        MarketTick(
            Broker.DERIV,
            "R_100",
            1_800_000_000 + index,
            Decimal(f"100.0{index % 10}"),
            start + timedelta(seconds=index),
            f"page-{index}",
            "TEST",
        )
        for index in range(250)
    )

    class PagingClient:
        def __init__(self) -> None:
            self.page_sizes: list[int] = []

        def market_history(
            self,
            symbol: str,
            *,
            style: str,
            count: int,
            timeframe_seconds: int | None = None,
            end_epoch: int | None = None,
        ) -> tuple[tuple[MarketTick, ...], tuple[object, ...]]:
            del timeframe_seconds
            assert symbol == "R_100" and style == "ticks"
            self.page_sizes.append(count)
            eligible = tuple(
                item for item in available if end_epoch is None or item.epoch <= end_epoch
            )
            return eligible[-count:], ()

    client = PagingClient()
    ticks = DerivTelemetryMonitor._paged_tick_history(
        cast(SocketWorkerClient, client),
        "R_100",
        count=250,
    )

    assert ticks == available
    assert client.page_sizes == [100, 100, 50]


class _RadarClient:
    capabilities = _Capabilities()

    def __init__(self, *, fail_symbol: str | None = None) -> None:
        self.fail_symbol = fail_symbol
        self.subscribed: list[str] = []
        self._start = datetime(2026, 8, 25, tzinfo=UTC)
        generator = random.Random(20260825)
        self._history = {
            "R_100": self._ticks("R_100", [digit for _ in range(250) for digit in (9, 0)]),
            "R_25": self._ticks(
                "R_25",
                [generator.randrange(10) for _ in range(500)],
            ),
        }

    def _ticks(self, symbol: str, digits: list[int]) -> tuple[MarketTick, ...]:
        return tuple(
            MarketTick(
                Broker.DERIV,
                symbol,
                1_900_000_000 + index,
                Decimal(f"100.0{digit}"),
                self._start + timedelta(seconds=index),
                f"{symbol}-{index}",
                "TEST",
            )
            for index, digit in enumerate(digits)
        )

    def broker_clock(self) -> BrokerClockSnapshot:
        return BrokerClockSnapshot(1_900_000_500, self._start, 0.01, Decimal("0.01"))

    def market_symbols(self) -> tuple[MarketSymbol, ...]:
        return tuple(
            MarketSymbol(
                Broker.DERIV,
                symbol,
                None,
                symbol,
                "synthetic_index",
                "random_index",
                "stockindex",
                Decimal("0.001"),
                True,
                self._start,
            )
            for symbol in ("R_25", "R_100")
        )

    def market_contracts(self, symbol: str) -> tuple[ContractMetadata, ...]:
        return (ContractMetadata(Broker.DERIV, symbol, "DIGITOVER", ("t",), 1, 10, True),)

    def subscribe_market_tick_snapshot(self, symbol: str) -> tuple[MarketTick, None]:
        if symbol == self.fail_symbol:
            raise RuntimeError("research stream unavailable")
        self.subscribed.append(symbol)
        return self._history[symbol][-1], None

    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> tuple[tuple[MarketTick, ...], tuple[object, ...]]:
        del timeframe_seconds
        assert style == "ticks"
        eligible = tuple(
            item for item in self._history[symbol] if end_epoch is None or item.epoch <= end_epoch
        )
        return eligible[-count:], ()


def test_shadow_universe_warms_assets_independently_and_ranks_without_execution() -> None:
    client = _RadarClient()
    supervisor = cast(ReadOnlyWorkerSupervisor, _Supervisor(client))  # type: ignore[arg-type]
    monitor = DerivTelemetryMonitor(
        supervisor,
        HealthGate(),
        DerivTelemetrySource.PUBLIC_LIVE,
        symbol_provider=lambda: "R_100",
    )

    monitor.probe_once()
    monitor._refresh_shadow_universe(force=True)
    monitor._ensure_tick_subscriptions()
    ranking = {item.symbol: item for item in monitor.snapshot.asset_ranking}

    assert set(client.subscribed) == {"R_25", "R_100"}
    assert ranking["R_100"].state is DigitAssetShadowState.CANDIDATE
    assert ranking["R_100"].selected is True
    assert ranking["R_25"].state is DigitAssetShadowState.MONITORING
    assert {item.last_signal_symbol for item in monitor.snapshot.synthetic_strategies} == {"R_100"}


def test_research_subscription_failure_does_not_disconnect_selected_asset() -> None:
    client = _RadarClient(fail_symbol="R_25")
    supervisor = cast(ReadOnlyWorkerSupervisor, _Supervisor(client))  # type: ignore[arg-type]
    reasons: list[str] = []
    gate = HealthGate()
    monitor = DerivTelemetryMonitor(
        supervisor,
        gate,
        DerivTelemetrySource.PUBLIC_LIVE,
        symbol_provider=lambda: "R_100",
        disconnect_notifier=reasons.append,
    )

    assert monitor.probe_once().connected is True
    monitor._refresh_shadow_universe(force=True)
    monitor._ensure_tick_subscriptions()

    assert monitor.snapshot.connected is True
    assert reasons == []
    assert not gate.contains("HG_MARKET_DATA_DISCONNECTED")
