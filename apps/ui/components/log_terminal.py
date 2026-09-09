from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from apps.ui.i18n import t
from apps.ui.theme import BG_ROOT, BORDER_ACCENT, TEXT_MUTED, TEXT_PRIMARY
from packages.protocol.ui_messages import UiOperationalLogEntry


class OperationalLogTerminal(QFrame):
    """Read-only terminal for the Core's bounded and sanitised event projection."""

    _SOURCE_GROUPS = ("ALL", "CORE", "DERIV", "IQOPTION", "WORKER", "OTHER")

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._entries: tuple[UiOperationalLogEntry, ...] = ()
        self._pending_entries: tuple[UiOperationalLogEntry, ...] = ()
        self._suppressed_keys: set[tuple[str, str, str, str, str]] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        self._title = QLabel()
        self._title.setObjectName("Title")
        heading.addWidget(self._title)
        heading.addStretch()
        self._count = QLabel()
        self._count.setStyleSheet(f"color: {TEXT_MUTED};")
        heading.addWidget(self._count)
        layout.addLayout(heading)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._level_filter = QComboBox()
        self._level_filter.currentIndexChanged.connect(self._render)
        toolbar.addWidget(self._level_filter)
        self._source_filter = QComboBox()
        self._source_filter.currentIndexChanged.connect(self._render)
        toolbar.addWidget(self._source_filter)
        self._search = QLineEdit()
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._render)
        toolbar.addWidget(self._search, 1)
        self._pause = QPushButton()
        self._pause.setCheckable(True)
        self._pause.toggled.connect(self._on_pause_toggled)
        toolbar.addWidget(self._pause)
        self._copy = QPushButton()
        self._copy.clicked.connect(self._copy_visible)
        toolbar.addWidget(self._copy)
        self._clear = QPushButton()
        self._clear.clicked.connect(self._clear_visible)
        toolbar.addWidget(self._clear)
        layout.addLayout(toolbar)

        self._terminal = QPlainTextEdit()
        self._terminal.setReadOnly(True)
        self._terminal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._terminal.document().setMaximumBlockCount(160)
        self._terminal.setMinimumHeight(330)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(9)
        self._terminal.setFont(mono)
        self._terminal.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {BG_ROOT}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER_ACCENT}; border-radius: 6px; padding: 8px; }}"
        )
        layout.addWidget(self._terminal, 1)

        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._notice.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self._notice)
        self.retranslate()

    @staticmethod
    def _key(entry: UiOperationalLogEntry) -> tuple[str, str, str, str, str]:
        return (
            entry.occurred_at_utc.isoformat(),
            entry.source,
            entry.event_name,
            entry.reason_code or "",
            entry.detail or "",
        )

    @classmethod
    def _source_group(cls, source: str) -> str:
        upper = source.upper()
        if "IQOPTION" in upper or "IQ_OPTION" in upper:
            return "IQOPTION"
        if "DERIV" in upper:
            return "DERIV"
        if "WORKER" in upper:
            return "WORKER"
        if upper == "CORE":
            return "CORE"
        return "OTHER"

    def update_entries(self, entries: Sequence[UiOperationalLogEntry]) -> None:
        incoming = tuple(entries)
        self._pending_entries = incoming
        current_keys = {self._key(entry) for entry in incoming}
        self._suppressed_keys.intersection_update(current_keys)
        if self._pause.isChecked() or incoming == self._entries:
            return
        self._entries = incoming
        self._render()

    def _filtered_lines(self) -> list[str]:
        level = str(self._level_filter.currentData() or "ALL")
        source = str(self._source_filter.currentData() or "ALL")
        query = self._search.text().strip().casefold()
        lines: list[str] = []
        for entry in self._entries:
            if self._key(entry) in self._suppressed_keys:
                continue
            if level != "ALL" and entry.level.value != level:
                continue
            if source != "ALL" and self._source_group(entry.source) != source:
                continue
            timestamp = entry.occurred_at_utc.astimezone().strftime("%H:%M:%S.%f")[:-3]
            parts = [
                timestamp,
                f"{entry.level.value:<7}",
                f"[{entry.source}]",
                entry.event_name,
            ]
            if entry.reason_code is not None:
                parts.append(f"reason={entry.reason_code}")
            if entry.detail is not None:
                parts.append(entry.detail)
            line = " ".join(parts)
            if query and query not in line.casefold():
                continue
            lines.append(line)
        return lines

    def _render(self) -> None:
        lines = self._filtered_lines()
        text = "\n".join(lines)
        if self._terminal.toPlainText() != text:
            self._terminal.setPlainText(text)
            scrollbar = self._terminal.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        self._count.setText(t("logs.count").format(visible=len(lines), total=len(self._entries)))

    def _on_pause_toggled(self, paused: bool) -> None:
        self._pause.setText(t("logs.resume") if paused else t("logs.pause"))
        if not paused and self._pending_entries != self._entries:
            self._entries = self._pending_entries
            self._render()

    def _copy_visible(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._terminal.toPlainText())

    def _clear_visible(self) -> None:
        self._suppressed_keys.update(self._key(entry) for entry in self._entries)
        self._render()

    def retranslate(self) -> None:
        selected_level = self._level_filter.currentData()
        selected_source = self._source_filter.currentData()
        self._title.setText(t("logs.title"))
        self._level_filter.clear()
        for key in ("ALL", "INFO", "WARNING", "ERROR"):
            self._level_filter.addItem(t(f"logs.level.{key.lower()}"), key)
        self._source_filter.clear()
        for key in self._SOURCE_GROUPS:
            self._source_filter.addItem(t(f"logs.source.{key.lower()}"), key)
        level_index = self._level_filter.findData(selected_level or "ALL")
        source_index = self._source_filter.findData(selected_source or "ALL")
        self._level_filter.setCurrentIndex(max(level_index, 0))
        self._source_filter.setCurrentIndex(max(source_index, 0))
        self._search.setPlaceholderText(t("logs.search"))
        self._pause.setText(t("logs.resume") if self._pause.isChecked() else t("logs.pause"))
        self._copy.setText(t("logs.copy"))
        self._clear.setText(t("logs.clear"))
        self._notice.setText(t("logs.notice"))
        self._render()

    @property
    def visible_text(self) -> str:
        return self._terminal.toPlainText()
