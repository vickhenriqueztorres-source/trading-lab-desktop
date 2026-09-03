from __future__ import annotations

# Palette: Obsidian Dark
BG_ROOT = "#080A0F"
BG_CARD = "#0E131F"
BG_SURFACE = "#161D2E"
BG_ELEVATED = "#1E293B"

BORDER_COLOR = "#232D42"
BORDER_ACCENT = "#334155"
BORDER_HOVER = "#00E5FF"

ACCENT_CYAN = "#00E5FF"
ACCENT_GREEN = "#00F59B"
ACCENT_RED = "#FF3366"
ACCENT_AMBER = "#FFB800"

TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#94A3B8"
TEXT_MUTED = "#64748B"

FONT_MAIN = '"Segoe UI"'
FONT_MONO = '"Consolas"'


def get_application_stylesheet() -> str:
    return f"""
    QMainWindow {{
        background-color: {BG_ROOT};
        color: {TEXT_PRIMARY};
    }}

    QWidget {{
        font-family: {FONT_MAIN};
        font-size: 13px;
        color: {TEXT_PRIMARY};
        background-color: transparent;
        selection-background-color: rgba(0, 229, 255, 0.30);
        selection-color: {TEXT_PRIMARY};
    }}

    QLineEdit, QComboBox, QAbstractSpinBox {{
        background-color: {BG_CARD};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACCENT};
        border-radius: 5px;
        padding: 2px 6px;
        min-height: 18px;
    }}

    QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{
        background-color: {BG_CARD};
        border: 1px solid {ACCENT_CYAN};
    }}

    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{
        background-color: rgba(14, 19, 31, 0.72);
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_COLOR};
    }}

    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        background-color: {BG_ELEVATED};
        border-left: 1px solid {BORDER_ACCENT};
        width: 18px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {BG_SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACCENT};
        selection-background-color: rgba(0, 229, 255, 0.22);
    }}

    QRadioButton {{
        color: {TEXT_PRIMARY};
        font-size: 13px;
        spacing: 6px;
    }}

    QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border-radius: 7px;
        border: 1px solid {BORDER_ACCENT};
        background-color: {BG_CARD};
    }}

    QRadioButton::indicator:checked {{
        background-color: {ACCENT_CYAN};
        border: 2px solid {BG_ROOT};
    }}

    QScrollArea {{
        background-color: {BG_ROOT};
        border: none;
    }}

    QFrame#HeaderBar {{
        background-color: {BG_CARD};
        border-bottom: 1px solid {BORDER_COLOR};
        padding: 8px 16px;
    }}

    QFrame#Card {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_COLOR};
        border-radius: 10px;
    }}

    QFrame#Card:hover {{
        border: 1px solid {BORDER_ACCENT};
    }}

    QFrame#Surface {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
    }}

    QFrame#DerivHero {{
        background-color: #101827;
        border: 1px solid #2A3B58;
        border-left: 3px solid {ACCENT_CYAN};
        border-radius: 10px;
    }}

    QFrame#StrategyRail {{
        background-color: #0B101A;
        border: 1px solid {BORDER_COLOR};
        border-radius: 10px;
    }}

    QLabel#Eyebrow {{
        color: {ACCENT_CYAN};
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 1.2px;
    }}

    QLabel#HeroTitle {{
        color: {TEXT_PRIMARY};
        font-size: 20px;
        font-weight: 800;
    }}

    QLabel#StatusPillOnline {{
        color: {ACCENT_GREEN};
        background-color: rgba(0, 245, 155, 0.10);
        border: 1px solid rgba(0, 245, 155, 0.38);
        border-radius: 10px;
        padding: 6px 10px;
        font-size: 10px;
        font-weight: 800;
    }}

    QLabel#StatusPillOffline {{
        color: {TEXT_SECONDARY};
        background-color: rgba(100, 116, 139, 0.10);
        border: 1px solid {BORDER_ACCENT};
        border-radius: 10px;
        padding: 6px 10px;
        font-size: 10px;
        font-weight: 800;
    }}

    QLabel#RailNote {{
        color: {TEXT_MUTED};
        background-color: rgba(0, 229, 255, 0.05);
        border: 1px solid rgba(0, 229, 255, 0.12);
        border-radius: 7px;
        padding: 10px;
        font-size: 10px;
    }}

    QLabel {{
        color: {TEXT_PRIMARY};
    }}

    QLabel#Title {{
        font-size: 16px;
        font-weight: bold;
        color: {TEXT_PRIMARY};
    }}

    QLabel#Subtitle {{
        font-size: 12px;
        color: {TEXT_SECONDARY};
    }}

    QLabel#ValueMono {{
        font-family: {FONT_MONO};
        font-size: 18px;
        font-weight: bold;
        color: {TEXT_PRIMARY};
    }}

    QLabel#GuidanceText {{
        color: {TEXT_SECONDARY};
        font-size: 12px;
        line-height: 1.4;
        padding: 4px 2px;
    }}

    QLabel#SafetyNotice {{
        background-color: rgba(255, 184, 0, 0.10);
        color: {ACCENT_AMBER};
        border: 1px solid rgba(255, 184, 0, 0.35);
        border-radius: 6px;
        padding: 10px;
        font-size: 12px;
        font-weight: 600;
    }}

    QLabel#BadgePractice {{
        background-color: rgba(255, 184, 0, 0.15);
        color: {ACCENT_AMBER};
        border: 1px solid rgba(255, 184, 0, 0.4);
        border-radius: 6px;
        padding: 4px 10px;
        font-weight: bold;
        font-size: 11px;
    }}

    QLabel#StatusReady {{
        color: {ACCENT_GREEN};
        font-weight: bold;
    }}

    QLabel#StatusDegraded {{
        color: {ACCENT_AMBER};
        font-weight: bold;
    }}

    QLabel#StatusStopped {{
        color: {ACCENT_RED};
        font-weight: bold;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {BG_SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        padding: 8px 16px;
        font-weight: 600;
        font-size: 12px;
    }}

    QPushButton:hover {{
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER_ACCENT};
    }}

    QPushButton:pressed {{
        background-color: {BG_ROOT};
    }}

    QPushButton:disabled {{
        background-color: rgba(30, 41, 59, 0.5);
        color: {TEXT_MUTED};
        border: 1px solid rgba(35, 45, 66, 0.5);
    }}

    /* Primary Accent Button */
    QPushButton#PrimaryButton {{
        background-color: rgba(0, 229, 255, 0.15);
        color: {ACCENT_CYAN};
        border: 1px solid rgba(0, 229, 255, 0.4);
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: rgba(0, 229, 255, 0.25);
        border: 1px solid {ACCENT_CYAN};
    }}

    /* Safe Stop Button */
    QPushButton#SafeStopButton {{
        background-color: rgba(255, 51, 102, 0.2);
        color: #FFFFFF;
        border: 1px solid {ACCENT_RED};
        border-radius: 8px;
        padding: 10px 20px;
        font-size: 13px;
        font-weight: bold;
        letter-spacing: 0.5px;
    }}

    QPushButton#SafeStopButton:hover {{
        background-color: rgba(255, 51, 102, 0.35);
        border: 1px solid #FF6688;
    }}

    QPushButton#SafeStopButton:pressed {{
        background-color: rgba(255, 51, 102, 0.5);
    }}

    QPushButton#BotStartButton {{
        background-color: rgba(0, 245, 155, 0.18);
        color: {ACCENT_GREEN};
        border: 1px solid {ACCENT_GREEN};
        border-radius: 8px;
        padding: 10px 20px;
        font-size: 13px;
        font-weight: bold;
        letter-spacing: 0.5px;
    }}

    QPushButton#BotStartButton:hover {{
        background-color: rgba(0, 245, 155, 0.30);
    }}

    QFrame#OutcomeCard {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
    }}

    QFrame#RiskSummary {{
        background-color: rgba(22, 29, 46, 0.78);
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
    }}

    QFrame#RiskMetricCard {{
        background-color: rgba(8, 10, 15, 0.46);
        border: 1px solid rgba(51, 65, 85, 0.78);
        border-radius: 7px;
    }}

    QLabel#MetricCaption {{
        color: {TEXT_MUTED};
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.4px;
    }}

    QLabel#MetricValue {{
        color: {TEXT_PRIMARY};
        font-family: {FONT_MONO};
        font-size: 17px;
        font-weight: 900;
    }}

    QLabel#MetricDetail {{
        color: {TEXT_SECONDARY};
        font-size: 9px;
    }}

    QLabel#RiskMetricValue {{
        color: {TEXT_PRIMARY};
        font-family: {FONT_MONO};
        font-size: 15px;
        font-weight: 900;
    }}

    QPushButton#StrategyButtonActive {{
        text-align: left;
        color: {TEXT_PRIMARY};
        background-color: rgba(0, 229, 255, 0.12);
        border: 1px solid rgba(0, 229, 255, 0.42);
        border-left: 3px solid {ACCENT_CYAN};
        border-radius: 8px;
        padding: 12px;
        font-size: 11px;
        font-weight: 800;
    }}

    QPushButton#StrategyButton {{
        text-align: left;
        color: {TEXT_MUTED};
        background-color: rgba(22, 29, 46, 0.50);
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        padding: 11px;
        font-size: 10px;
        font-weight: 700;
    }}

    QPushButton#StrategyButton:disabled {{
        color: {TEXT_MUTED};
        background-color: rgba(14, 19, 31, 0.65);
        border: 1px dashed {BORDER_COLOR};
    }}

    QTabWidget#StrategyTabs::pane {{
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        background-color: {BG_ROOT};
    }}

    /* Language Switcher Button */
    QPushButton#LangButton {{
        background-color: {BG_SURFACE};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER_COLOR};
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: bold;
    }}

    QPushButton#LangButton:checked {{
        background-color: rgba(0, 229, 255, 0.2);
        color: {ACCENT_CYAN};
        border: 1px solid {ACCENT_CYAN};
    }}

    /* Primary and nested navigation */
    QTabWidget::pane {{
        border: none;
        border-top: 1px solid {BORDER_COLOR};
        background-color: {BG_ROOT};
    }}

    QTabBar::tab {{
        background-color: {BG_CARD};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER_COLOR};
        border-bottom: none;
        padding: 10px 16px;
        margin-right: 2px;
        min-width: 90px;
        font-weight: 600;
    }}

    QTabBar::tab:selected {{
        background-color: {BG_SURFACE};
        color: {ACCENT_CYAN};
        border-top: 2px solid {ACCENT_CYAN};
    }}

    QTabBar::tab:focus {{
        border: 1px solid {ACCENT_CYAN};
    }}

    QTabBar::tab:hover:!selected {{
        color: {TEXT_PRIMARY};
        background-color: {BG_ELEVATED};
    }}

    /* Table View */
    QTableWidget {{
        background-color: {BG_CARD};
        gridline-color: {BORDER_COLOR};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        selection-background-color: rgba(0, 229, 255, 0.15);
        selection-color: {TEXT_PRIMARY};
        font-family: {FONT_MONO};
        font-size: 12px;
    }}

    QHeaderView::section {{
        background-color: {BG_SURFACE};
        color: {TEXT_SECONDARY};
        border: none;
        border-bottom: 1px solid {BORDER_COLOR};
        border-right: 1px solid {BORDER_COLOR};
        padding: 6px;
        font-family: {FONT_MAIN};
        font-size: 11px;
        font-weight: bold;
    }}

    QProgressBar {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: 4px;
        text-align: center;
        color: {TEXT_PRIMARY};
        font-family: {FONT_MONO};
        font-size: 11px;
    }}

    QProgressBar::chunk {{
        background-color: {ACCENT_CYAN};
        border-radius: 3px;
    }}

    QToolTip {{
        background-color: {BG_ELEVATED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACCENT};
        padding: 4px 8px;
        border-radius: 4px;
        font-family: {FONT_MAIN};
        font-size: 11px;
    }}

    QScrollBar:vertical {{
        background: {BG_CARD};
        width: 8px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical {{
        background: {BORDER_COLOR};
        min-height: 20px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {BORDER_ACCENT};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    """
