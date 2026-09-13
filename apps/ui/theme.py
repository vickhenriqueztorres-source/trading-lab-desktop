from __future__ import annotations

from apps.ui.design.tokens import TOKENS

# Exposição das constantes para compatibilidade retroativa
BG_ROOT = TOKENS.BG_ROOT
BG_CARD = TOKENS.BG_CARD
BG_SURFACE = TOKENS.BG_SURFACE
BG_ELEVATED = TOKENS.BG_ELEVATED

BORDER_COLOR = TOKENS.BORDER_COLOR
BORDER_ACCENT = TOKENS.BORDER_ACCENT
BORDER_HOVER = TOKENS.BORDER_HOVER

ACCENT_PRIMARY = TOKENS.ACCENT_PRIMARY
ACCENT_CYAN = TOKENS.ACCENT_PRIMARY  # Alias para compatibilidade: substitui ciano por azul-petróleo
ACCENT_GREEN = TOKENS.ACCENT_GREEN
ACCENT_RED = TOKENS.ACCENT_RED
ACCENT_AMBER = TOKENS.ACCENT_AMBER

TEXT_PRIMARY = TOKENS.TEXT_PRIMARY
TEXT_SECONDARY = TOKENS.TEXT_SECONDARY
TEXT_MUTED = TOKENS.TEXT_MUTED

FONT_MAIN = TOKENS.FONT_MAIN
FONT_MONO = TOKENS.FONT_MONO

RADIUS_SM = TOKENS.RADIUS_SM
RADIUS_MD = TOKENS.RADIUS_MD
RADIUS_LG = TOKENS.RADIUS_LG

SPACE_1 = TOKENS.SPACE_1
SPACE_2 = TOKENS.SPACE_2
SPACE_3 = TOKENS.SPACE_3
SPACE_4 = TOKENS.SPACE_4


def _lighten_hex(hex_color: str, factor: float = 0.10) -> str:
    """Calcula uma cor mais clara para estados de hover sem recorrer a gradientes."""
    hex_clean = hex_color.lstrip("#")
    r = int(hex_clean[0:2], 16)
    g = int(hex_clean[2:4], 16)
    b = int(hex_clean[4:6], 16)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02X}{g:02X}{b:02X}"


PRIMARY_HOVER = _lighten_hex(ACCENT_GREEN, 0.10)


def get_application_stylesheet() -> str:
    return f"""
    /* Janela principal e base de widgets */
    QMainWindow, QWidget#root {{
        background-color: {BG_ROOT};
        color: {TEXT_PRIMARY};
    }}

    QWidget {{
        font-family: {FONT_MAIN};
        font-size: 13px;
        color: {TEXT_PRIMARY};
        background-color: transparent;
        selection-background-color: {BG_ELEVATED};
        selection-color: {TEXT_PRIMARY};
    }}

    /* Cards e Containers */
    QFrame#card, QFrame[card="true"], QFrame#Card {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_MD}px;
    }}

    QFrame#Surface {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
    }}

    QFrame#HeaderBar {{
        background-color: {BG_CARD};
        border-bottom: 1px solid {BORDER_COLOR};
        padding: 8px 16px;
    }}

    QFrame#DerivHero {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_ACCENT};
        border-left: 3px solid {ACCENT_PRIMARY};
        border-radius: {RADIUS_MD}px;
    }}

    QFrame#StrategyRail {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_MD}px;
    }}

    QFrame#OutcomeCard {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
    }}

    QFrame#RiskSummary {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
    }}

    QFrame#RiskMetricCard {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
    }}

    /* Botões */
    QPushButton {{
        background-color: {BG_SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_MD}px;
        padding: 8px 16px;
        font-weight: 600;
        font-size: 12px;
    }}

    QPushButton:hover {{
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER_HOVER};
    }}

    QPushButton:pressed {{
        background-color: {BG_ROOT};
    }}

    QPushButton:disabled {{
        background-color: {BG_ELEVATED};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_COLOR};
    }}

    /* Botão primário */
    QPushButton#primary, QPushButton#PrimaryButton, QPushButton#BotStartButton {{
        background-color: {ACCENT_GREEN};
        color: {BG_ROOT};
        font-weight: bold;
        border: none;
        border-radius: {RADIUS_MD}px;
        min-height: 44px;
        padding: 0 16px;
        font-size: 13px;
    }}

    QPushButton#primary:hover,
    QPushButton#PrimaryButton:hover,
    QPushButton#BotStartButton:hover {{
        background-color: {PRIMARY_HOVER};
        border: none;
    }}

    QPushButton#primary:disabled,
    QPushButton#PrimaryButton:disabled,
    QPushButton#BotStartButton:disabled {{
        background-color: {BG_ELEVATED};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_COLOR};
    }}

    /* Botão secundário */
    QPushButton#secondary {{
        background-color: transparent;
        border: 1px solid {BORDER_ACCENT};
        color: {TEXT_PRIMARY};
        border-radius: {RADIUS_MD}px;
        padding: 8px 16px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#secondary:hover {{
        border: 1px solid {BORDER_HOVER};
        background-color: {BG_ELEVATED};
    }}

    /* Botão perigo / Safe Stop */
    QPushButton#danger, QPushButton#SafeStopButton {{
        background-color: transparent;
        border: 1px solid {ACCENT_RED};
        color: {ACCENT_RED};
        border-radius: {RADIUS_MD}px;
        padding: 8px 16px;
        font-weight: bold;
        font-size: 12px;
    }}

    QPushButton#danger:hover, QPushButton#SafeStopButton:hover {{
        background-color: rgba(229, 72, 77, 0.15);
        border: 1px solid {ACCENT_RED};
    }}

    QPushButton#danger:pressed, QPushButton#SafeStopButton:pressed {{
        background-color: rgba(229, 72, 77, 0.25);
    }}

    /* Botões de estratégia */
    QPushButton#StrategyButtonActive {{
        text-align: left;
        color: {TEXT_PRIMARY};
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER_HOVER};
        border-left: 3px solid {ACCENT_PRIMARY};
        border-radius: {RADIUS_SM}px;
        padding: 12px;
        font-size: 11px;
        font-weight: 800;
    }}

    QPushButton#StrategyButton {{
        text-align: left;
        color: {TEXT_MUTED};
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
        padding: 11px;
        font-size: 10px;
        font-weight: 700;
    }}

    QPushButton#StrategyButton:hover {{
        border: 1px solid {BORDER_ACCENT};
        color: {TEXT_SECONDARY};
    }}

    /* Botão do seletor de idioma */
    QPushButton#LangButton {{
        background-color: {BG_SURFACE};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: bold;
    }}

    QPushButton#LangButton:checked {{
        background-color: {BG_ELEVATED};
        color: {ACCENT_PRIMARY};
        border: 1px solid {ACCENT_PRIMARY};
    }}

    /* Campos de entrada: QLineEdit, QComboBox, QSpinBox (36px altura, padding 0 12px) */
    QLineEdit, QComboBox, QAbstractSpinBox {{
        background-color: {BG_SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
        min-height: 36px;
        max-height: 36px;
        padding: 0 12px;
        font-size: 13px;
    }}

    QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER_HOVER};
    }}

    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{
        background-color: {BG_CARD};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_COLOR};
    }}

    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        background-color: {BG_ELEVATED};
        border-left: 1px solid {BORDER_COLOR};
        width: 18px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {BG_SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACCENT};
        selection-background-color: {BG_ELEVATED};
    }}

    /* Radio buttons */
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
        background-color: {ACCENT_PRIMARY};
        border: 2px solid {BG_ROOT};
    }}

    /* ScrollArea */
    QScrollArea {{
        background-color: {BG_ROOT};
        border: none;
    }}

    /* Tabs primárias e aninhadas */
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
        color: {ACCENT_PRIMARY};
        border-top: 2px solid {ACCENT_PRIMARY};
    }}

    QTabBar::tab:focus {{
        border: 1px solid {BORDER_HOVER};
    }}

    QTabBar::tab:hover:!selected {{
        color: {TEXT_PRIMARY};
        background-color: {BG_ELEVATED};
    }}

    QTabWidget#StrategyTabs::pane {{
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
        background-color: {BG_ROOT};
    }}

    /* Tabelas (QTableView / QTableWidget) */
    QTableView, QTableWidget {{
        background-color: {BG_CARD};
        gridline-color: {BORDER_COLOR};
        border: 1px solid {BORDER_COLOR};
        border-radius: {RADIUS_SM}px;
        color: {TEXT_PRIMARY};
        font-family: {FONT_MONO};
        font-size: 12px;
        alternate-background-color: {BG_SURFACE};
        selection-background-color: {BG_ELEVATED};
        selection-color: {TEXT_PRIMARY};
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
        font-weight: 600;
        text-transform: uppercase;
    }}

    /* ScrollBars */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}

    QScrollBar::handle:vertical {{
        background: {BORDER_ACCENT};
        min-height: 20px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {BORDER_HOVER};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
        background: transparent;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0;
    }}

    QScrollBar::handle:horizontal {{
        background: {BORDER_ACCENT};
        min-width: 20px;
        border-radius: 4px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background: {BORDER_HOVER};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
        background: transparent;
    }}

    /* QToolTip */
    QToolTip {{
        background-color: {BG_ELEVATED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACCENT};
        padding: 8px;
        border-radius: {RADIUS_SM}px;
        font-family: {FONT_MAIN};
        font-size: 11px;
    }}

    /* Progress bar */
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
        background-color: {ACCENT_PRIMARY};
        border-radius: 3px;
    }}

    /* Labels utilitárias por objectName */
    QLabel#kpiValue {{
        font-family: {FONT_MONO};
        font-size: 28px;
        font-weight: bold;
        color: {TEXT_PRIMARY};
    }}

    QLabel#kpiLabel {{
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        color: {TEXT_SECONDARY};
    }}

    QLabel#sectionTitle {{
        font-size: 16px;
        font-weight: 600;
        color: {TEXT_PRIMARY};
    }}

    QLabel#hint {{
        font-size: 12px;
        color: {TEXT_MUTED};
    }}

    QLabel#mono {{
        font-family: {FONT_MONO};
    }}

    /* Labels existentes do sistema */
    QLabel {{
        color: {TEXT_PRIMARY};
    }}

    QLabel#Eyebrow {{
        color: {ACCENT_PRIMARY};
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 1.2px;
    }}

    QLabel#HeroTitle {{
        color: {TEXT_PRIMARY};
        font-size: 20px;
        font-weight: 800;
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

    QLabel#StatusPillOnline {{
        color: {ACCENT_GREEN};
        background-color: rgba(31, 181, 122, 0.12);
        border: 1px solid rgba(31, 181, 122, 0.40);
        border-radius: 10px;
        padding: 6px 10px;
        font-size: 10px;
        font-weight: 800;
    }}

    QLabel#StatusPillOffline {{
        color: {TEXT_SECONDARY};
        background-color: rgba(95, 112, 128, 0.12);
        border: 1px solid {BORDER_ACCENT};
        border-radius: 10px;
        padding: 6px 10px;
        font-size: 10px;
        font-weight: 800;
    }}

    QLabel#RailNote {{
        color: {TEXT_MUTED};
        background-color: rgba(58, 167, 184, 0.06);
        border: 1px solid rgba(58, 167, 184, 0.18);
        border-radius: 7px;
        padding: 10px;
        font-size: 10px;
    }}

    QLabel#SafetyNotice {{
        background-color: rgba(217, 162, 27, 0.12);
        color: {ACCENT_AMBER};
        border: 1px solid rgba(217, 162, 27, 0.35);
        border-radius: 6px;
        padding: 10px;
        font-size: 12px;
        font-weight: 600;
    }}

    QLabel#BadgePractice {{
        background-color: rgba(217, 162, 27, 0.15);
        color: {ACCENT_AMBER};
        border: 1px solid rgba(217, 162, 27, 0.40);
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
    """
