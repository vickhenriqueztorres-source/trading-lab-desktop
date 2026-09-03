"""UI tests for ManifestStrategyPanelWidget and strategy cards (R-BOT-11, I-13)."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

# Ensure headless Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from apps.ui.components.manifest_strategy_panel import (
    ManifestStrategyPanelWidget,
)
from apps.ui.theme import get_application_stylesheet


class MockValidatedStats:
    def __init__(
        self,
        p_hat: str = "0.582",
        wilson_lower: str = "0.565",
        p_min: str = "0.540",
        payout_min: str = "0.85",
        ops_per_day: str = "14",
        worst_streak: int = 4,
        res_1000: str = "1250",
        n: int = 1000,
    ) -> None:
        self.p_hat = Decimal(p_hat)
        self.wilson_lower = Decimal(wilson_lower)
        self.p_min_at_validation = Decimal(p_min)
        self.payout_min = Decimal(payout_min)
        self.ops_per_day = Decimal(ops_per_day)
        self.worst_streak = worst_streak
        self.result_1000_ops_stake10 = Decimal(res_1000)
        self.score = Decimal("4.8")
        self.n = n


class MockStrategyEntry:
    def __init__(
        self,
        key: str,
        display_name_pt: str,
        asset: str,
        status: str,
        timeframe: str = "M1",
        hours_utc: tuple[int, int] = (0, 6),
        reason_pt: str = "",
        val_stats: MockValidatedStats | None = None,
    ) -> None:
        self.key = key
        self.family = "F1"
        self.display_name_pt = display_name_pt
        self.asset = asset
        self.timeframe = timeframe
        self.hours_utc = hours_utc
        self.status = status
        self.reason_pt = reason_pt
        self.validated = val_stats or MockValidatedStats()
        self.params: dict[str, Any] = {}


@pytest.fixture
def sample_manifest() -> dict[str, Any]:
    return {
        "manifest_version": 2,
        "strategies": (
            MockStrategyEntry(
                key="s1_approved",
                display_name_pt="Reversão Bollinger",
                asset="EURUSD",
                status="approved",
                hours_utc=(0, 6),
                val_stats=MockValidatedStats(
                    p_hat="0.585", wilson_lower="0.565", p_min="0.540", worst_streak=4, n=1000
                ),
            ),
            MockStrategyEntry(
                key="s2_observation",
                display_name_pt="Pullback de Tendência",
                asset="GBPUSD",
                status="observation",
                hours_utc=(8, 16),
                val_stats=MockValidatedStats(
                    p_hat="0.572", wilson_lower="0.550", p_min="0.535", worst_streak=5, n=1000
                ),
            ),
            MockStrategyEntry(
                key="s3_blocked",
                display_name_pt="Rejeição de Nível",
                asset="USDJPY",
                status="approved",
                hours_utc=(22, 4),
                val_stats=MockValidatedStats(
                    p_hat="0.590", wilson_lower="0.570", p_min="0.545", worst_streak=3, n=1000
                ),
            ),
            MockStrategyEntry(
                key="s4_rejected",
                display_name_pt="Rompimento de Range",
                asset="EURJPY",
                status="rejected",
                reason_pt="PBO acima do limite tolerado (24% > 20%).",
            ),
        ),
    }


def test_manifest_strategy_panel_renders_cards_and_five_numbers(
    sample_manifest: dict[str, Any],
) -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget(account_type="DEMO")
    panel.setStyleSheet(get_application_stylesheet())
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    # Active cards: s1_approved, s2_observation, s3_blocked (s4_rejected in secondary panel)
    card1 = panel.get_card("s1_approved")
    assert card1 is not None

    # Verify header format: "nome pt-BR · asset · TF · faixa horária"
    assert "Reversão Bollinger · EURUSD · M1 · 00:00–06:00 UTC" in card1._header_label.text()

    # Verify badge status
    assert "Aprovada" in card1._status_badge.text()

    # Verify 5 fundamental numbers
    # 1. Taxa de acerto validada {p_hat}% (mínimo necessário {p_min}%)
    assert "Taxa de acerto validada 58.5% (mínimo necessário 54.0%)" in card1._stat1_label.text()
    # 2. Margem de segurança +{margem} pp (0.565 - 0.540 = +2.5 pp)
    assert "Margem de segurança +2.5 pp" in card1._stat2_label.text()
    # 3. Operações por dia ~{ops}
    assert "Operações por dia ~14" in card1._stat3_label.text()
    # 4. Pior sequência de perdas {streak} (em {n} operações)
    assert "Pior sequência de perdas 4 (em 1.000 operações)" in card1._stat4_label.text()
    # 5. Resultado em 1.000 ops {valor} com stake $10, sem MG
    assert "Resultado em 1.000 ops $1.250 com stake $10, sem MG" in card1._stat5_label.text()

    panel.close()


def test_observation_disabled_in_real_mode(sample_manifest: dict[str, Any]) -> None:
    """R-BOT-8: Observation strategies can only be turned on in Demo accounts."""
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget(account_type="REAL")
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    card_obs = panel.get_card("s2_observation")
    assert card_obs is not None
    # In REAL mode, observation button MUST be disabled
    assert card_obs._toggle_button.isEnabled() is False
    expected_tip = "Estratégias em observação só podem ser ligadas em conta Demo."
    assert expected_tip in card_obs._toggle_button.toolTip()

    # Approved strategy remains enabled in REAL mode
    card_app = panel.get_card("s1_approved")
    assert card_app is not None
    assert card_app._toggle_button.isEnabled() is True

    # Switching to DEMO enables observation strategy
    panel.set_account_type("DEMO")
    app.processEvents()
    assert card_obs._toggle_button.isEnabled() is True

    panel.close()


def test_selection_modes_single_and_multi(sample_manifest: dict[str, Any]) -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget(account_type="DEMO")
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    card1 = panel.get_card("s1_approved")
    card2 = panel.get_card("s2_observation")
    assert card1 is not None and card2 is not None

    # In SINGLE mode (default): turning on card 1, then card 2 turns off card 1
    panel.set_selection_mode("SINGLE")
    card1._toggle_button.click()
    app.processEvents()
    assert card1._is_active is True
    assert card1._toggle_button.text() == "Desligar"

    card2._toggle_button.click()
    app.processEvents()
    assert card2._is_active is True
    assert card1._is_active is False
    assert card1._toggle_button.text() == "Ligar"

    # In MULTI mode: both can be active simultaneously
    panel.set_selection_mode("MULTI")
    card1._toggle_button.click()
    app.processEvents()
    assert card1._is_active is True
    assert card2._is_active is True

    panel.close()


def test_live_states_and_payout_gate_reason(sample_manifest: dict[str, Any]) -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget()
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    card3 = panel.get_card("s3_blocked")
    assert card3 is not None

    # Default state is Monitorando
    assert "Monitorando" in card3._live_status_label.text()

    # Set state to Signal
    panel.update_live_status("s3_blocked", "Sinal", "")
    assert "Sinal" in card3._live_status_label.text()

    # Set state to Blocked by payout gate
    panel.update_live_status(
        "s3_blocked",
        "Bloqueada",
        "Opera com payout ≥ 85%. Agora: 80% — aguardando.",
    )
    expected_status = "Bloqueada — Opera com payout ≥ 85%. Agora: 80% — aguardando."
    assert expected_status in card3._live_status_label.text()

    # Set state to Rebaixada pelo monitor (SPRT)
    panel.update_live_status("s3_blocked", "Rebaixada pelo monitor", "")
    assert "Rebaixada pelo monitor" in card3._live_status_label.text()

    panel.close()


def test_secondary_rejected_panel(sample_manifest: dict[str, Any]) -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget()
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    assert panel._rejected_panel.isVisible() is True
    # Verify s4_rejected reason is displayed in the secondary panel
    rej_text = panel._rejected_panel.findChildren(QLabel)
    all_rej_text = " ".join(lbl.text() for lbl in rej_text)
    assert "Rompimento de Range" in all_rej_text
    assert "PBO acima do limite tolerado (24% > 20%)." in all_rej_text

    panel.close()


def test_manifest_expiration_alert_banner() -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget()
    panel.show()
    app.processEvents()

    # Initially hidden
    assert panel._banner.isVisible() is False

    # Show expiration alert
    panel.show_manifest_alert(version=1, status_text="expirado", age_text="há 2 dias")
    app.processEvents()
    assert panel._banner.isVisible() is True
    assert "v1 expirado (há 2 dias)" in panel._banner._text_label.text()

    # Hide alert
    panel.hide_manifest_alert()
    app.processEvents()
    assert panel._banner.isVisible() is False

    panel.close()


def test_collapsible_details_toggle(sample_manifest: dict[str, Any]) -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget()
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    card1 = panel.get_card("s1_approved")
    assert card1 is not None

    # Initially collapsed
    assert card1._details_container.isVisible() is False
    assert card1._details_button.text() == "Ver detalhes ▸"

    # Click expand
    card1._details_button.click()
    app.processEvents()
    assert card1._details_container.isVisible() is True
    assert card1._details_button.text() == "Ocultar detalhes ▾"
    assert "Payout mínimo exigido: 85%" in card1._detail_payout.text()
    expected_windows = "Janelas de validação: treino 6m ancorado / teste 2m rolando"
    assert expected_windows in card1._detail_windows.text()
    assert "Holdout / Out-of-Sample: 20% estrito e intocado" in card1._detail_holdout.text()
    assert "Versão do manifesto de origem: v2" in card1._detail_manifest.text()

    # Click collapse
    card1._details_button.click()
    app.processEvents()
    assert card1._details_container.isVisible() is False
    assert card1._details_button.text() == "Ver detalhes ▸"

    panel.close()


def test_prohibited_text_strict_scan(sample_manifest: dict[str, Any]) -> None:
    """I-13: Scan all labels in the panel to strictly enforce transparency rules."""
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget()
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    labels = panel.findChildren(QLabel)
    buttons = panel.findChildren(QPushButton)
    all_texts = [lbl.text() for lbl in labels if lbl.text()] + [
        btn.text() for btn in buttons if btn.text()
    ]

    combined_text = " \n ".join(all_texts).lower()

    # Forbidden terms
    forbidden_terms = ["lucro garantido", "sem risco"]
    for term in forbidden_terms:
        assert term not in combined_text, f"Forbidden term found in UI: {term}"

    # Verify no standalone '100%' claim
    assert "100%" not in combined_text, "Found forbidden 100% claim in UI"

    # Verify every occurrence of 'taxa de acerto' includes '(mínimo necessário'
    for t in all_texts:
        if "taxa de acerto" in t.lower():
            assert "mínimo necessário" in t.lower(), f"Rate without required minimum: {t}"
        if "pior sequência de perdas" in t.lower():
            assert "operações)" in t.lower(), f"Worst streak without operation count n: {t}"

    panel.close()


def test_screenshot_capture_with_three_distinct_states(
    sample_manifest: dict[str, Any], tmp_path: Path
) -> None:
    """Acceptance criteria: Capture panel image with >= 3 cards in distinct states."""
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget(account_type="DEMO")
    panel.setStyleSheet(get_application_stylesheet())
    panel.resize(960, 640)
    panel.set_manifest(sample_manifest)

    # Set 3 cards into distinct states:
    # Card 1: Approved & Monitoring
    panel.update_live_status("s1_approved", "Monitorando", "")
    card1 = panel.get_card("s1_approved")
    if card1:
        card1.set_active(True)

    # Card 2: Observation & Demoted by SPRT
    panel.update_live_status("s2_observation", "Rebaixada pelo monitor", "")

    # Card 3: Blocked by Payout Gate
    panel.update_live_status(
        "s3_blocked",
        "Bloqueada",
        "Opera com payout ≥ 85%. Agora: 80% — aguardando.",
    )

    # Expand details of card 1 for rich view
    if card1:
        card1._toggle_details()

    panel.show()
    app.processEvents()

    # Capture panel using PySide6 offscreen grab
    pixmap = panel.grab()
    assert not pixmap.isNull()
    assert pixmap.width() >= 600
    assert pixmap.height() >= 400

    # Save artifact to docs/artifacts/
    artifact_dir = Path("docs/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifact_dir / "strategy_cards_panel.png"
    success = pixmap.save(str(out_path), "PNG")
    assert success is True
    assert out_path.exists()
    assert out_path.stat().st_size > 1000  # Non-trivial image file

    panel.close()


def test_turn_on_all_and_turn_off_all_bulk_actions(sample_manifest: dict[str, Any]) -> None:
    app = QApplication.instance() or QApplication([])
    panel = ManifestStrategyPanelWidget()
    panel.set_manifest(sample_manifest)
    panel.show()
    app.processEvents()

    # 1. Click Turn On All
    panel._btn_turn_on_all.click()
    assert panel._selection_mode == "MULTI"
    assert panel._radio_multi.isChecked()
    active_cards = [c for c in panel._cards.values() if c._is_active]
    # Approved cards should all be active
    assert len(active_cards) >= 2

    # 2. Click Turn Off All
    panel._btn_turn_off_all.click()
    active_cards_after = [c for c in panel._cards.values() if c._is_active]
    assert len(active_cards_after) == 0

    panel.close()

