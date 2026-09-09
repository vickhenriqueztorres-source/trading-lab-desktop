from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from apps.ui.components.log_terminal import OperationalLogTerminal
from packages.protocol import UiLogLevel, UiOperationalLogEntry


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _entry(
    seconds: int,
    level: UiLogLevel,
    source: str,
    event_name: str,
    reason_code: str | None = None,
) -> UiOperationalLogEntry:
    return UiOperationalLogEntry(
        datetime(2026, 9, 9, 12, tzinfo=UTC) + timedelta(seconds=seconds),
        level,
        source,
        event_name,
        reason_code,
    )


def test_terminal_filters_pauses_and_clears_only_the_local_view(qapp: QApplication) -> None:
    terminal = OperationalLogTerminal()
    first = _entry(1, UiLogLevel.INFO, "CORE", "core_started")
    warning = _entry(
        2,
        UiLogLevel.WARNING,
        "IQOPTION",
        "health_gate_blocked",
        "MD_CLOCK_UNTRUSTED",
    )
    terminal.update_entries((first, warning))
    assert "core_started" in terminal.visible_text
    assert "MD_CLOCK_UNTRUSTED" in terminal.visible_text

    terminal._level_filter.setCurrentIndex(terminal._level_filter.findData("WARNING"))
    assert "core_started" not in terminal.visible_text
    assert "health_gate_blocked" in terminal.visible_text

    terminal._level_filter.setCurrentIndex(terminal._level_filter.findData("ALL"))
    terminal._source_filter.setCurrentIndex(terminal._source_filter.findData("IQOPTION"))
    assert "core_started" not in terminal.visible_text
    terminal._source_filter.setCurrentIndex(terminal._source_filter.findData("ALL"))

    terminal._pause.setChecked(True)
    third = _entry(3, UiLogLevel.ERROR, "DERIV", "order_rejected")
    terminal.update_entries((first, warning, third))
    assert "order_rejected" not in terminal.visible_text
    terminal._pause.setChecked(False)
    assert "order_rejected" in terminal.visible_text

    terminal._clear.click()
    assert terminal.visible_text == ""
    fourth = _entry(4, UiLogLevel.INFO, "CORE", "recovery_completed")
    terminal.update_entries((first, warning, third, fourth))
    assert terminal.visible_text.endswith("recovery_completed")
    assert "core_started" not in terminal.visible_text
    terminal.close()
