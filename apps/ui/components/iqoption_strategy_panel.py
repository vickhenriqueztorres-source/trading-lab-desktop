from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from apps.ui.i18n import t
from packages.protocol import UiIqOptionAssetRank, UiIqOptionRiskConfig


class IqOptionStrategyConfigWidget(QFrame):
    """Visible RSI selection, bounded Practice risk controls and 2-column layout."""

    config_apply_requested = Signal(object)
    dirty_state_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._projected_config: UiIqOptionRiskConfig | None = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._practice = True
        self._available_asset_signature: tuple[tuple[str, str, str, str], ...] = ()
        self._is_dirty: bool = False
        self._saved_state: dict[str, Any] = {}
        self._tracking_initialized: bool = False

        self.setObjectName("Surface")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        # 1. Header (Compact)
        hdr_box = QVBoxLayout()
        hdr_box.setSpacing(2)
        self._title = QLabel()
        self._title.setObjectName("Title")
        self._title.setStyleSheet("font-size: 15px; font-weight: 700;")
        hdr_box.addWidget(self._title)

        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._notice.setObjectName("SafetyNotice")
        self._notice.setStyleSheet("font-size: 11px; color: #94A3B8;")
        hdr_box.addWidget(self._notice)
        root_layout.addLayout(hdr_box)

        # 2. Two-column Layout (Zero Scroll)
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # === CARD 1: Estratégia, Ativo & Martingale (Left) ===
        card_strat = QFrame()
        card_strat.setObjectName("Surface")
        card_strat.setStyleSheet(
            "QFrame#Surface { background: #0B1118; border: 1px solid #1E293B; border-radius: 8px; }"
        )
        lay_strat = QVBoxLayout(card_strat)
        lay_strat.setContentsMargins(14, 10, 14, 10)
        lay_strat.setSpacing(8)

        # Internal compatibility mode dropdown (kept for tests and internal sync)
        self._mode = QComboBox()
        self._mode.addItems(["SINGLE", "AUTO"])
        self._mode.setVisible(False)

        # --- Sub-seção 1: Estratégia & Escolha do Ativo ---
        self._sub_strat_title = QLabel(t("iq.risk.subsection_strategy_asset"))
        self._sub_strat_title.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #38BDF8; letter-spacing: 0.3px;"
        )
        lay_strat.addWidget(self._sub_strat_title)

        form_strat_asset = QFormLayout()
        form_strat_asset.setSpacing(6)
        form_strat_asset.setContentsMargins(0, 0, 0, 0)

        self._strategy = QComboBox()
        self._strategy.addItem(
            t("iq.risk.strategy_hack_chino_desc"), "iqoption-hack-chino"
        )
        self._strategy.addItem(t("iq.risk.strategy_catalog_auto_desc"), "AUTO")
        self._lbl_strategy_title = QLabel(t("iq.risk.strategy"))
        form_strat_asset.addRow(self._lbl_strategy_title, self._strategy)

        self._symbol = QComboBox()
        self._symbol.addItem(t("iq.strategy.auto_all_assets"), "AUTO")
        self._lbl_asset_title = QLabel(t("iq.risk.asset"))
        form_strat_asset.addRow(self._lbl_asset_title, self._symbol)

        self._timeframe = QLabel("⏱️ M1 · Expiração 1 min")
        self._timeframe.setStyleSheet("color: #E2E8F0; font-weight: 600; font-size: 11px;")
        self._lbl_timeframe_title = QLabel(t("iq.strategy.timeframe_readonly"))
        form_strat_asset.addRow(self._lbl_timeframe_title, self._timeframe)

        lay_strat.addLayout(form_strat_asset)

        # Contextual Auto Mode Hint
        self._auto_radar_hint = QLabel(t("iq.strategy.auto_radar_hint"))
        self._auto_radar_hint.setWordWrap(True)
        self._auto_radar_hint.setStyleSheet(
            "background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25); "
            "border-radius: 6px; padding: 6px 8px; color: #38BDF8; font-size: 11px;"
        )
        lay_strat.addWidget(self._auto_radar_hint)

        # Subtle elegant divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(
            "background-color: #1E293B; border: none; min-height: 1px; "
            "max-height: 1px; margin: 2px 0;"
        )
        lay_strat.addWidget(divider)

        # --- Sub-seção 2: Entrada & Gerenciamento de Martingale ---
        self._sub_martingale_title = QLabel(t("iq.risk.subsection_stake_martingale"))
        self._sub_martingale_title.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #F59E0B; letter-spacing: 0.3px;"
        )
        lay_strat.addWidget(self._sub_martingale_title)

        form_martingale = QFormLayout()
        form_martingale.setSpacing(6)
        form_martingale.setContentsMargins(0, 0, 0, 0)

        self._stake = self._money_spin(1.00, 100.00, 1.00)
        self._lbl_stake_title = QLabel(t("iq.risk.stake"))
        form_martingale.addRow(self._lbl_stake_title, self._stake)

        self._martingale = QComboBox()
        self._martingale.addItem(t("iq.risk.martingale.off"), 0)
        self._martingale.addItem(t("iq.risk.martingale.g1"), 1)
        self._martingale.addItem(t("iq.risk.martingale.g2"), 2)
        self._lbl_martingale_title = QLabel(t("iq.risk.martingale"))
        form_martingale.addRow(self._lbl_martingale_title, self._martingale)

        self._martingale_multiplier = QDoubleSpinBox()
        self._martingale_multiplier.setDecimals(2)
        self._martingale_multiplier.setRange(1.10, 3.00)
        self._martingale_multiplier.setSingleStep(0.10)
        self._martingale_multiplier.setSuffix("×")
        self._martingale_multiplier.setValue(2.00)
        self._lbl_multiplier_title = QLabel(t("iq.risk.martingale_multiplier"))
        form_martingale.addRow(self._lbl_multiplier_title, self._martingale_multiplier)

        self._martingale_max_stake = self._money_spin(1.00, 10_000.00, 4.00)
        self._lbl_max_stake_title = QLabel(t("iq.risk.martingale_cap"))
        form_martingale.addRow(self._lbl_max_stake_title, self._martingale_max_stake)

        lay_strat.addLayout(form_martingale)

        self._martingale_projection = QLabel()
        self._martingale_projection.setWordWrap(True)
        self._martingale_projection.setStyleSheet(
            "background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.25); "
            "border-radius: 6px; padding: 6px 8px; color: #F59E0B; font-size: 11px;"
        )
        lay_strat.addWidget(self._martingale_projection)
        cols.addWidget(card_strat, 1)

        # === CARD 2: Limites de Risco & Proteção (Right) ===
        card_risk = QFrame()
        card_risk.setObjectName("Surface")
        card_risk.setStyleSheet(
            "QFrame#Surface { background: #0B1118; border: 1px solid #1E293B; border-radius: 8px; }"
        )
        lay_risk = QVBoxLayout(card_risk)
        lay_risk.setContentsMargins(14, 10, 14, 10)
        lay_risk.setSpacing(6)

        self._sec_limits_title = QLabel(t("iq.risk.section_limits"))
        self._sec_limits_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #1FB57A;")
        lay_risk.addWidget(self._sec_limits_title)

        form_risk = QFormLayout()
        form_risk.setSpacing(5)
        form_risk.setContentsMargins(0, 0, 0, 0)

        self._daily_stop = self._money_spin(0.01, 10_000.00, 10.00)
        form_risk.addRow(t("iq.risk.daily_stop"), self._daily_stop)

        self._daily_take = self._money_spin(0.01, 10_000.00, 10.00)
        form_risk.addRow(t("iq.risk.daily_take"), self._daily_take)

        self._losses = QSpinBox()
        self._losses.setRange(1, 10)
        form_risk.addRow(t("iq.risk.losses"), self._losses)

        self._cooldown = QSpinBox()
        self._cooldown.setRange(0, 3600)
        self._cooldown.setSuffix(" s")
        form_risk.addRow(t("iq.risk.cooldown"), self._cooldown)

        self._daily_trades = QSpinBox()
        self._daily_trades.setRange(1, 100)
        form_risk.addRow(t("iq.risk.daily_trades"), self._daily_trades)

        lay_risk.addLayout(form_risk)

        self._protection_tip = QLabel(t("iq.risk.core_protection_tip"))
        self._protection_tip.setWordWrap(True)
        self._protection_tip.setStyleSheet(
            "background: rgba(31, 181, 122, 0.08); border: 1px solid rgba(31, 181, 122, 0.25); "
            "border-radius: 6px; padding: 6px 8px; color: #1FB57A; font-size: 11px;"
        )
        lay_risk.addWidget(self._protection_tip)
        cols.addWidget(card_risk, 1)

        root_layout.addLayout(cols)

        # 3. Action Footer Bar (Large Save Button & Dirty Warning)
        footer = QVBoxLayout()
        footer.setSpacing(5)

        self._unsaved_banner = QLabel(t("iq.risk.unsaved_banner"))
        self._unsaved_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._unsaved_banner.setStyleSheet(
            "background: rgba(245, 158, 11, 0.15); border: 1px solid #F59E0B; "
            "border-radius: 6px; padding: 6px 12px; color: #F59E0B; font-weight: 700; "
            "font-size: 12px;"
        )
        self._unsaved_banner.setVisible(False)
        footer.addWidget(self._unsaved_banner)

        self._apply = QPushButton()
        self._apply.setObjectName("PrimarySaveButton")
        self._apply.setFixedHeight(48)
        self._apply.clicked.connect(self._emit_config)
        footer.addWidget(self._apply)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setObjectName("Subtitle")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.addWidget(self._status)

        root_layout.addLayout(footer)

        # Connect functional sync signals
        self._mode.currentIndexChanged.connect(self._sync_selection)
        self._strategy.currentIndexChanged.connect(self._sync_selection)
        self._symbol.currentIndexChanged.connect(self._sync_selection)
        self._martingale.currentIndexChanged.connect(self._update_martingale_projection)
        self._martingale_multiplier.valueChanged.connect(self._update_martingale_projection)
        self._martingale_max_stake.valueChanged.connect(self._update_martingale_projection)
        self._stake.valueChanged.connect(self._update_martingale_projection)

        # Connect dirty state tracking signals
        self._mode.currentIndexChanged.connect(self._on_field_changed)
        self._strategy.currentIndexChanged.connect(self._on_field_changed)
        self._symbol.currentIndexChanged.connect(self._on_field_changed)
        self._stake.valueChanged.connect(self._on_field_changed)
        self._martingale.currentIndexChanged.connect(self._on_field_changed)
        self._martingale_multiplier.valueChanged.connect(self._on_field_changed)
        self._martingale_max_stake.valueChanged.connect(self._on_field_changed)
        self._daily_stop.valueChanged.connect(self._on_field_changed)
        self._daily_take.valueChanged.connect(self._on_field_changed)
        self._losses.valueChanged.connect(self._on_field_changed)
        self._cooldown.valueChanged.connect(self._on_field_changed)
        self._daily_trades.valueChanged.connect(self._on_field_changed)

        self.set_config(UiIqOptionRiskConfig())
        self.retranslate()
        self._tracking_initialized = True
        self._saved_state = self._get_current_state_dict()
        self._update_save_button_appearance()

    def _get_current_state_dict(self) -> dict[str, Any]:
        return {
            "mode": str(self._mode.currentText()),
            "strategy": str(self._strategy.currentData() or ""),
            "symbol": str(self._symbol.currentData() or ""),
            "stake": round(self._stake.value(), 2),
            "martingale": int(self._martingale.currentData() or 0),
            "multiplier": round(self._martingale_multiplier.value(), 2),
            "max_stake": round(self._martingale_max_stake.value(), 2),
            "daily_stop": round(self._daily_stop.value(), 2),
            "daily_take": round(self._daily_take.value(), 2),
            "losses": int(self._losses.value()),
            "cooldown": int(self._cooldown.value()),
            "daily_trades": int(self._daily_trades.value()),
        }

    def _on_field_changed(self) -> None:
        if not self._tracking_initialized or not self._saved_state:
            return
        is_dirty = self._get_current_state_dict() != self._saved_state
        if is_dirty != self._is_dirty:
            self._is_dirty = is_dirty
            self._update_save_button_appearance()
            self.dirty_state_changed.emit(self._is_dirty)

    def _update_save_button_appearance(self) -> None:
        if self._is_dirty:
            self._unsaved_banner.setVisible(True)
            self._apply.setText(t("iq.risk.save_btn_dirty"))
            self._apply.setStyleSheet(
                "QPushButton { "
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 #1FB57A, stop:1 #10B981); "
                "color: #FFFFFF; font-size: 14px; font-weight: 800; "
                "border-radius: 8px; border: 2px solid #34D399; min-height: 48px; padding: 0 16px; "
                "} "
                "QPushButton:hover { "
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 #26CF8C, stop:1 #13C98D); "
                "border-color: #6EE7B7; "
                "} "
                "QPushButton:pressed { "
                "background: #0E7A53; "
                "}"
            )
        else:
            self._unsaved_banner.setVisible(False)
            self._apply.setText(t("iq.risk.save_btn_saved"))
            self._apply.setStyleSheet(
                "QPushButton { "
                "background: rgba(31, 181, 122, 0.15); color: #1FB57A; "
                "font-size: 13px; font-weight: 700; "
                "border-radius: 8px; border: 1px solid #1FB57A; min-height: 48px; padding: 0 16px; "
                "} "
                "QPushButton:hover { "
                "background: rgba(31, 181, 122, 0.28); "
                "} "
                "QPushButton:pressed { "
                "background: rgba(31, 181, 122, 0.40); "
                "}"
            )

    def has_unsaved_changes(self) -> bool:
        return self._is_dirty

    def discard_unsaved_changes(self) -> None:
        if not self._saved_state:
            return
        self._tracking_initialized = False
        try:
            saved = self._saved_state
            self._mode.setCurrentText(saved.get("mode", "AUTO"))
            strat_idx = self._strategy.findData(saved.get("strategy"))
            if strat_idx >= 0:
                self._strategy.setCurrentIndex(strat_idx)
            sym_idx = self._symbol.findData(saved.get("symbol"))
            if sym_idx >= 0:
                self._symbol.setCurrentIndex(sym_idx)
            self._stake.setValue(saved.get("stake", 1.0))
            mg_idx = self._martingale.findData(saved.get("martingale"))
            if mg_idx >= 0:
                self._martingale.setCurrentIndex(mg_idx)
            self._martingale_multiplier.setValue(saved.get("multiplier", 2.0))
            self._martingale_max_stake.setValue(saved.get("max_stake", 4.0))
            self._daily_stop.setValue(saved.get("daily_stop", 10.0))
            self._daily_take.setValue(saved.get("daily_take", 10.0))
            self._losses.setValue(saved.get("losses", 3))
            self._cooldown.setValue(saved.get("cooldown", 0))
            self._daily_trades.setValue(saved.get("daily_trades", 100))
            self._sync_selection()
            self._update_martingale_projection()
        finally:
            self._tracking_initialized = True
            self._is_dirty = False
            self._update_save_button_appearance()
            self.dirty_state_changed.emit(False)

    def save_changes(self) -> None:
        self._emit_config()

    def set_available_assets(self, ranking: Sequence[UiIqOptionAssetRank]) -> None:
        """Replace the selector with the broker catalogue projected by Core."""

        signature = tuple(
            (
                item.symbol,
                item.display_name,
                item.readiness,
                "DISCOVERY" if item.status == "DISCOVERY_ONLY" else "EXEC",
            )
            for item in ranking
        )
        if signature == self._available_asset_signature:
            return
        self._available_asset_signature = signature
        selected = str(self._symbol.currentData() or "AUTO")
        projected = None if self._projected_config is None else self._projected_config.symbol
        wanted = projected if projected not in {None, "AUTO"} else selected
        self._symbol.blockSignals(True)
        self._symbol.clear()
        open_count = sum(
            1 for item in ranking if item.readiness == "READY" and item.status != "DISCOVERY_ONLY"
        )
        auto_text = (
            f"🌐 {t('iq.strategy.auto_all_assets')} ({open_count} ativos abertos)"
            if open_count > 0
            else f"🌐 {t('iq.strategy.auto_all_assets')}"
        )
        self._symbol.addItem(auto_text, "AUTO")
        model = self._symbol.model()
        for item in ranking:
            executable = item.readiness == "READY" and item.status != "DISCOVERY_ONLY"
            icon = "🟢" if executable else "🔒"
            suffix = "" if executable else " (Fechado)"
            self._symbol.addItem(f"{icon} {item.display_name}{suffix}", item.symbol)
            row = self._symbol.count() - 1
            if isinstance(model, QStandardItemModel):
                option = model.item(row)
                if option is not None:
                    option.setEnabled(executable)
                    option.setToolTip(item.candidate_details)
        if wanted and self._symbol.findData(wanted) < 0:
            self._symbol.addItem(f"🔒 {wanted} · não disponível nesta sessão", wanted)
            if isinstance(model, QStandardItemModel):
                option = model.item(self._symbol.count() - 1)
                if option is not None:
                    option.setEnabled(False)
        target = "AUTO" if selected == "AUTO" else wanted
        self._symbol.setCurrentIndex(max(0, self._symbol.findData(target)))
        self._symbol.blockSignals(False)
        self._sync_selection()

    def set_manifest(self, manifest: dict[str, Any]) -> None:
        selected = self._strategy.currentData()
        self._entries = {
            str(e["key"]): e
            for e in manifest.get("strategies", [])
            if isinstance(e, dict)
            and "key" in e
            and e.get("status") in {"approved", "observation"}
            and e.get("family") in {"F1", "F2", "F3", "F4", "F5"}
        }
        self._strategy.blockSignals(True)
        self._strategy.clear()
        self._strategy.addItem(
            t("iq.risk.strategy_hack_chino_desc"), "iqoption-hack-chino"
        )
        self._strategy.addItem(t("iq.risk.strategy_catalog_auto_desc"), "AUTO")
        for key, entry in sorted(self._entries.items()):
            self._strategy.addItem(str(entry.get("display_name_pt", key)), key)
        self._strategy.setCurrentIndex(max(0, self._strategy.findData(selected)))
        self._strategy.blockSignals(False)
        self._sync_selection()

    def set_account_type(self, account_type: str) -> None:
        self._practice = account_type.upper() not in {"REAL", "LIVE"}
        self._sync_selection()

    def _sync_selection(self) -> None:
        if self.sender() is self._mode:
            automatic = self._mode.currentText() == "AUTO"
        else:
            automatic = self._symbol.currentData() == "AUTO"
        strat_key = str(self._strategy.currentData() or "")
        local_keys = {
            "iqoption-hack-chino",
        }
        local = strat_key in local_keys
        entry = self._entries.get(strat_key)

        wanted_mode = "AUTO" if automatic else "SINGLE"
        if self._mode.currentText() != wanted_mode:
            self._mode.blockSignals(True)
            self._mode.setCurrentText(wanted_mode)
            self._mode.blockSignals(False)

        if automatic and self._symbol.currentData() != "AUTO":
            auto_idx = self._symbol.findData("AUTO")
            if auto_idx >= 0:
                self._symbol.blockSignals(True)
                self._symbol.setCurrentIndex(auto_idx)
                self._symbol.blockSignals(False)

        if local:
            self._strategy.setEnabled(True)
            self._symbol.setEnabled(True)
            self._timeframe.setText("⏱️ M1 · Expiração 1 min")
        elif automatic:
            self._strategy.setEnabled(strat_key == "AUTO")
            self._symbol.setEnabled(strat_key == "AUTO")
            self._timeframe.setText("AUTO" if entry is None else str(entry["timeframe"]))
        elif entry is not None:
            self._strategy.setEnabled(True)
            asset = str(entry["asset"])
            if self._symbol.findData(asset) < 0:
                self._symbol.addItem(asset, asset)
            self._symbol.blockSignals(True)
            self._symbol.setCurrentIndex(self._symbol.findData(asset))
            self._symbol.blockSignals(False)
            self._symbol.setEnabled(False)
            self._timeframe.setText(str(entry["timeframe"]))
        else:
            self._strategy.setEnabled(True)
            self._symbol.setEnabled(True)
            self._timeframe.setText("AUTO" if automatic else "M1")

        if hasattr(self, "_auto_radar_hint"):
            self._auto_radar_hint.setVisible(automatic)

        model = self._strategy.model()
        if isinstance(model, QStandardItemModel):
            for index in range(self._strategy.count()):
                item_data = str(self._strategy.itemData(index))
                option = model.item(index)
                if option is not None:
                    if item_data in local_keys:
                        option.setEnabled(self._practice)
                    else:
                        recipe = self._entries.get(item_data)
                        if recipe is not None:
                            option.setEnabled(self._practice or recipe.get("status") == "approved")

        has_approved_recipe = not local and entry is not None and entry.get("status") == "approved"
        self._apply.setEnabled(self._practice or has_approved_recipe)

    @staticmethod
    def _money_spin(minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(0.10)
        spin.setPrefix("USD ")
        spin.setValue(value)
        return spin

    def set_config(self, config: UiIqOptionRiskConfig) -> None:
        if config == self._projected_config:
            return
        self._tracking_initialized = False
        self._projected_config = config
        self._mode.blockSignals(True)
        self._strategy.blockSignals(True)
        self._symbol.blockSignals(True)
        self._mode.setCurrentText("AUTO" if config.symbol == "AUTO" else "SINGLE")
        target_strat = config.strategy_id
        if target_strat in {
            "iqoption-rsi-demo",
            "iqoption-hour-of-day",
            "iqoption-body-gap-fill",
            "iqoption-liquidity-gap",
            "iqoption-pattern-reversal",
            "iqoption-extreme-rejection",
            "iqoption-microtrend-scalper",
        }:
            target_strat = "iqoption-hack-chino"
        if self._strategy.findData(target_strat) < 0:
            self._strategy.addItem(target_strat, target_strat)
        if config.symbol != "AUTO" and self._symbol.findData(config.symbol) < 0:
            self._symbol.addItem(f"{config.symbol} · aguardando catálogo", config.symbol)
        self._strategy.setCurrentIndex(max(0, self._strategy.findData(target_strat)))
        self._symbol.setCurrentIndex(max(0, self._symbol.findData(config.symbol)))
        self._stake.setValue(config.stake_minor_units / 100)
        self._daily_stop.setValue(config.daily_stop_loss_minor_units / 100)
        self._daily_take.setValue(config.daily_take_profit_minor_units / 100)
        self._losses.setValue(config.max_consecutive_losses)
        self._cooldown.setValue(config.cooldown_seconds_after_loss)
        self._daily_trades.setValue(config.max_daily_trades)
        self._martingale.setCurrentIndex(
            max(0, self._martingale.findData(config.martingale_max_steps))
            if config.martingale_enabled
            else 0
        )
        self._martingale_multiplier.setValue(config.martingale_multiplier_basis_points / 10_000)
        self._martingale_max_stake.setValue(config.martingale_max_stake_minor_units / 100)
        self._mode.blockSignals(False)
        self._strategy.blockSignals(False)
        self._symbol.blockSignals(False)
        self._sync_selection()
        self._update_martingale_projection()
        local_keys = {
            "iqoption-hack-chino",
        }
        if (
            config.symbol != "AUTO"
            and config.strategy_id not in local_keys
            and config.strategy_id not in self._entries
        ):
            self._timeframe.setText({60: "M1", 300: "M5", 900: "M15"}[config.timeframe_seconds])
        self._saved_state = self._get_current_state_dict()
        self._is_dirty = False
        self._tracking_initialized = True
        self._update_save_button_appearance()

    def set_apply_result(
        self, accepted: bool, reason: str | None = None, rearmed: bool = False
    ) -> None:
        if accepted:
            self._saved_state = self._get_current_state_dict()
            self._is_dirty = False
            self._update_save_button_appearance()
            self.dirty_state_changed.emit(False)
            if rearmed:
                self._status.setText(t("iq.risk.saved_active"))
                self._status.setStyleSheet("color: #1FB57A; font-weight: bold; font-size: 12px;")
            else:
                self._status.setText(t("iq.risk.saved_disarmed"))
                self._status.setStyleSheet("color: #1FB57A; font-weight: bold; font-size: 12px;")
        else:
            self._status.setText(t("iq.risk.rejected", reason=reason or "UNKNOWN"))
            self._status.setStyleSheet("color: #E5484D; font-weight: bold; font-size: 12px;")

    def retranslate(self) -> None:
        self._title.setText(t("iq.risk.title"))
        self._notice.setText(t("iq.risk.notice"))
        if hasattr(self, "_sec_strat_title"):
            self._sec_strat_title.setText(t("iq.risk.section_strategy"))
        if hasattr(self, "_sub_strat_title"):
            self._sub_strat_title.setText(t("iq.risk.subsection_strategy_asset"))
        if hasattr(self, "_sub_martingale_title"):
            self._sub_martingale_title.setText(t("iq.risk.subsection_stake_martingale"))
        if hasattr(self, "_sec_limits_title"):
            self._sec_limits_title.setText(t("iq.risk.section_limits"))
        if hasattr(self, "_protection_tip"):
            self._protection_tip.setText(t("iq.risk.core_protection_tip"))
        if hasattr(self, "_auto_radar_hint"):
            self._auto_radar_hint.setText(t("iq.strategy.auto_radar_hint"))
        if hasattr(self, "_unsaved_banner"):
            self._unsaved_banner.setText(t("iq.risk.unsaved_banner"))
        self._update_save_button_appearance()
        if self._strategy.count() > 0:
            idx_hc = self._strategy.findData("iqoption-hack-chino")
            if idx_hc >= 0:
                self._strategy.setItemText(idx_hc, t("iq.risk.strategy_hack_chino_desc"))
            idx_cat = self._strategy.findData("AUTO")
            if idx_cat >= 0:
                self._strategy.setItemText(idx_cat, t("iq.risk.strategy_catalog_auto_desc"))
        if hasattr(self, "_lbl_strategy_title"):
            self._lbl_strategy_title.setText(t("iq.risk.strategy"))
        if hasattr(self, "_lbl_asset_title"):
            self._lbl_asset_title.setText(t("iq.risk.asset"))
        if hasattr(self, "_lbl_timeframe_title"):
            self._lbl_timeframe_title.setText(t("iq.strategy.timeframe_readonly"))
        if hasattr(self, "_lbl_stake_title"):
            self._lbl_stake_title.setText(t("iq.risk.stake"))
        if hasattr(self, "_lbl_martingale_title"):
            self._lbl_martingale_title.setText(t("iq.risk.martingale"))
        if hasattr(self, "_lbl_multiplier_title"):
            self._lbl_multiplier_title.setText(t("iq.risk.martingale_multiplier"))
        if hasattr(self, "_lbl_max_stake_title"):
            self._lbl_max_stake_title.setText(t("iq.risk.martingale_cap"))
        if not self._status.text():
            self._status.setText(t("iq.risk.ready"))

    def _update_martingale_projection(self) -> None:
        steps = int(self._martingale.currentData() or 0)
        enabled = steps > 0
        self._martingale_multiplier.setEnabled(enabled)
        self._martingale_max_stake.setEnabled(enabled)
        if not enabled:
            self._martingale_projection.setText(t("iq.risk.martingale_disabled"))
            return
        current = round(self._stake.value() * 100)
        cap = round(self._martingale_max_stake.value() * 100)
        multiplier = self._martingale_multiplier.value()
        stakes = [current]
        for _step in range(steps):
            current = min(
                int(
                    (Decimal(current) * Decimal(str(multiplier))).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                ),
                cap,
            )
            stakes.append(current)
        sequence = " · ".join(
            f"G{index} USD {amount / 100:.2f}" for index, amount in enumerate(stakes)
        )
        self._martingale_projection.setText(
            t(
                "iq.risk.martingale_projection",
                sequence=sequence,
                exposure=f"{sum(stakes) / 100:.2f}",
            )
        )

    def _emit_config(self) -> None:
        self._sync_selection()
        data = self._strategy.currentData()
        automatic = (self._symbol.currentData() == "AUTO") or (self._mode.currentText() == "AUTO")
        selected_strategy = str(data) if data else "AUTO"
        local_keys = {
            "iqoption-hack-chino",
        }
        if selected_strategy in local_keys and not self._practice:
            return
        entry = self._entries.get(selected_strategy)
        steps = int(self._martingale.currentData() or 0)
        strategy_id = (
            selected_strategy
            if selected_strategy in local_keys
            else ("AUTO" if automatic else selected_strategy)
        )
        duration_seconds = 60
        try:
            config = UiIqOptionRiskConfig(
                strategy_id=strategy_id,
                symbol="AUTO" if automatic else str(self._symbol.currentData()),
                timeframe_seconds=60
                if entry is None or automatic
                else {"M1": 60, "M5": 300, "M15": 900}[entry["timeframe"]],
                duration_seconds=duration_seconds,
                stake_minor_units=round(self._stake.value() * 100),
                daily_stop_loss_minor_units=round(self._daily_stop.value() * 100),
                daily_take_profit_minor_units=round(self._daily_take.value() * 100),
                max_consecutive_losses=self._losses.value(),
                cooldown_seconds_after_loss=self._cooldown.value(),
                max_daily_trades=self._daily_trades.value(),
                martingale_enabled=steps > 0,
                martingale_multiplier_basis_points=int(
                    Decimal(str(self._martingale_multiplier.value())) * Decimal(10_000)
                ),
                martingale_max_steps=max(1, steps),
                martingale_max_stake_minor_units=round(self._martingale_max_stake.value() * 100),
            )
        except ValueError as exc:
            self._status.setText(t("iq.risk.rejected", reason=str(exc)))
            return
        self.config_apply_requested.emit(config)
