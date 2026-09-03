"""Generate rich screenshot of ManifestStrategyPanelWidget with >= 3 distinct states."""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

# Do not force offscreen dummy font engine if running on Windows
if "QT_QPA_PLATFORM" in os.environ and os.environ["QT_QPA_PLATFORM"] == "offscreen":
    del os.environ["QT_QPA_PLATFORM"]

from PySide6.QtWidgets import QApplication

from apps.ui.components.manifest_strategy_panel import ManifestStrategyPanelWidget
from apps.ui.theme import get_application_stylesheet


class MockValidatedStats:
    def __init__(
        self,
        p_hat: str = "0.585",
        wilson_lower: str = "0.565",
        p_min: str = "0.540",
        payout_min: str = "0.85",
        ops_per_day: str = "15",
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
        self.params: dict[str, object] = {}


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    panel = ManifestStrategyPanelWidget(account_type="DEMO")
    panel.setStyleSheet(get_application_stylesheet())
    panel.resize(1020, 1200)

    manifest = {
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

    panel.set_manifest(manifest)

    # State 1: Approved & Monitoring (Active/Ligar)
    card1 = panel.get_card("s1_approved")
    if card1:
        card1.set_active(True)
    panel.update_live_status("s1_approved", "Monitorando")

    # State 2: Observation & Demoted by SPRT
    panel.update_live_status("s2_observation", "Rebaixada pelo monitor")

    # State 3: Blocked by Payout Gate
    panel.update_live_status(
        "s3_blocked",
        "Bloqueada",
        "Opera com payout ≥ 85%. Agora: 80% — aguardando.",
    )

    panel.show()
    app.processEvents()

    pixmap = panel.grab()
    out_dir = Path("docs/artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "strategy_cards_panel.png"
    pixmap.save(str(out_path), "PNG")
    print(f"Saved screenshot to {out_path} ({out_path.stat().st_size} bytes)")
    panel.close()


if __name__ == "__main__":
    main()
