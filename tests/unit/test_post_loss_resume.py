from __future__ import annotations

from datetime import UTC, datetime

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from tests.unit.test_deriv_auto_trader import _performance_rows, _Runtime, _telemetry


def test_ratchet_is_capped_at_configured_pp() -> None:
    class Reader:
        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return []

        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            return _performance_rows(
                [100] * 8 + [-450] * 2,
                datetime(2026, 8, 28, 11, 49, tzinfo=UTC),
            )

    runtime = _Runtime()
    runtime.reader = Reader()
    trader = DerivDigitAutoTrader(
        runtime,
        "DEMO",
        _telemetry,
        utc_clock=lambda: datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is True
    capped = [
        item
        for item in runtime.event_sink.events
        if item.event_name == "performance_ratchet_capped"
    ]
    assert capped
    fields = dict(capped[-1].fields)
    assert fields["performance_ratchet_capped"] is True
    assert fields["required_applied"] == "73.0"


def test_performance_window_expires_old_trades() -> None:
    class Reader:
        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return []

        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            return _performance_rows(
                [-500] * 20,
                datetime(2026, 8, 26, tzinfo=UTC),
            )

    runtime = _Runtime()
    runtime.reader = Reader()
    trader = DerivDigitAutoTrader(
        runtime, "DEMO", _telemetry, utc_clock=lambda: datetime(2026, 8, 28, tzinfo=UTC)
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is True


def test_signals_are_not_burned_during_gate_block() -> None:
    class Reader:
        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return []

        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            return _performance_rows(
                [100] * 8 + [-450] * 2,
                datetime(2026, 8, 28, tzinfo=UTC),
            )

    runtime = _Runtime()
    runtime.reader = Reader()
    trader = DerivDigitAutoTrader(
        runtime, "DEMO", _telemetry, utc_clock=lambda: datetime(2026, 8, 28, tzinfo=UTC)
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is False
    assert trader._evaluated_signal_keys == set()


def test_manual_resume_resets_recovery_and_performance_state() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(runtime, "DEMO", _telemetry)  # type: ignore[arg-type]
    trader._set_reason("BOT_PERFORMANCE_COOLDOWN")
    assert trader.manual_resume() is True
    assert any(
        item.event_name == "digit_operator_manual_resume" for item in runtime.event_sink.events
    )


def test_gate_block_reason_exposes_numbers() -> None:
    class Reader:
        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return []

        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            return _performance_rows(
                [100] * 8 + [-450] * 2,
                datetime(2026, 8, 28, tzinfo=UTC),
            )

    runtime = _Runtime()
    runtime.reader = Reader()
    trader = DerivDigitAutoTrader(
        runtime, "DEMO", _telemetry, utc_clock=lambda: datetime(2026, 8, 28, tzinfo=UTC)
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is False
    description = trader.waiting_status.description
    assert "exigido" in description
    assert "estimado" in description
    assert "P&L janela" in description
    assert "operações" in description
    assert "sonda" in description
