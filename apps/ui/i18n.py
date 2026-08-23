from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import ClassVar

TRANSLATIONS: dict[str, dict[str, str]] = {
    # App Header & Branding
    "app.title": {
        "en": "Trading Lab Desktop",
        "es": "Trading Lab Desktop",
    },
    "app.practice_badge": {
        "en": "PRACTICE MODE",
        "es": "MODO PRÁCTICA",
    },
    "app.practice_subtitle": {
        "en": "NO REAL CAPITAL AT RISK",
        "es": "SIN RIESGO DE CAPITAL REAL",
    },
    "app.latency": {
        "en": "Core IPC Latency",
        "es": "Latencia IPC Core",
    },
    "app.status.connected": {
        "en": "CORE CONNECTED",
        "es": "CORE CONECTADO",
    },
    "app.status.disconnected": {
        "en": "CORE DISCONNECTED",
        "es": "CORE DESCONECTADO",
    },
    # Top Cockpit KPIs
    "kpi.global_exposure": {
        "en": "Global Exposure",
        "es": "Exposición Global",
    },
    "kpi.daily_pnl": {
        "en": "Daily Realized P&L",
        "es": "P&L Diario Realizado",
    },
    "kpi.consecutive_losses": {
        "en": "Consecutive Losses",
        "es": "Pérdidas Consecutivas",
    },
    "kpi.risk_state": {
        "en": "Risk State",
        "es": "Estado de Riesgo",
    },
    "kpi.global_state": {
        "en": "System State",
        "es": "Estado del Sistema",
    },
    # Broker Hub
    "broker.hub_title": {
        "en": "Broker Hub",
        "es": "Central de Corredores",
    },
    "broker.connected": {
        "en": "CONNECTED",
        "es": "CONECTADO",
    },
    "broker.disconnected": {
        "en": "DISCONNECTED",
        "es": "DESCONECTADO",
    },
    "broker.clock_synced": {
        "en": "Clock Synchronized",
        "es": "Reloj Sincronizado",
    },
    "broker.clock_untrusted": {
        "en": "Clock Untrusted",
        "es": "Reloj No Confiable",
    },
    "broker.balance": {
        "en": "Balance",
        "es": "Saldo",
    },
    "broker.unavailable": {
        "en": "UNAVAILABLE",
        "es": "NO DISPONIBLE",
    },
    "mode.PRACTICE": {
        "en": "PRACTICE",
        "es": "PRÁCTICA",
    },
    "mode.DEMO_READ_ONLY": {
        "en": "DEMO READ-ONLY",
        "es": "DEMO SOLO LECTURA",
    },
    # Main navigation and broker isolation
    "tabs.overview": {
        "en": "Overview",
        "es": "Vista general",
    },
    "tabs.deriv": {
        "en": "Deriv",
        "es": "Deriv",
    },
    "tabs.iq_option": {
        "en": "IQ Option",
        "es": "IQ Option",
    },
    "tabs.activity": {
        "en": "Activity",
        "es": "Actividad",
    },
    "tabs.settings": {
        "en": "Settings",
        "es": "Configuración",
    },
    "tabs.status": {
        "en": "Status",
        "es": "Estado",
    },
    "tabs.configuration": {
        "en": "Configuration",
        "es": "Configuración",
    },
    "overview.intro": {
        "en": (
            "Consolidated projection. Open each broker tab to see its status and activity "
            "without mixing Deriv and IQ Option."
        ),
        "es": (
            "Proyección consolidada. Abra la pestaña de cada corredor para ver su estado y "
            "actividad sin mezclar Deriv e IQ Option."
        ),
    },
    "activity.intro": {
        "en": (
            "Authoritative activity projected by the Core. Open, unknown and reconciling "
            "orders remain visible here."
        ),
        "es": (
            "Actividad autoritativa proyectada por el Core. Las órdenes abiertas, desconocidas "
            "y en reconciliación permanecen visibles aquí."
        ),
    },
    "broker.deriv.intro": {
        "en": (
            "Deriv is monitored independently. A Deriv failure does not hide the IQ Option "
            "projection; global blockers are shown in Overview."
        ),
        "es": (
            "Deriv se supervisa de forma independiente. Una falla de Deriv no oculta la "
            "proyección de IQ Option; los bloqueos globales aparecen en Vista general."
        ),
    },
    "broker.iq_option.intro": {
        "en": (
            "IQ Option is monitored independently. An IQ Option failure does not hide the "
            "Deriv projection; global blockers are shown in Overview."
        ),
        "es": (
            "IQ Option se supervisa de forma independiente. Una falla de IQ Option no oculta "
            "la proyección de Deriv; los bloqueos globales aparecen en Vista general."
        ),
    },
    # Configuration explanations
    "config.read_only_title": {
        "en": "Effective broker configuration",
        "es": "Configuración efectiva del corredor",
    },
    "config.scope": {
        "en": "Scope",
        "es": "Alcance",
    },
    "config.effective_mode": {
        "en": "Effective mode confirmed by the Core",
        "es": "Modo efectivo confirmado por el Core",
    },
    "config.waiting_projection": {
        "en": "Waiting for a verified projection",
        "es": "Esperando una proyección verificada",
    },
    "config.no_real_mode": {
        "en": (
            "Real mode is not available in this version. No setting on this screen can enable it."
        ),
        "es": (
            "El modo real no está disponible en esta versión. Ningún ajuste de esta pantalla "
            "puede activarlo."
        ),
    },
    "config.deriv.body": {
        "en": (
            "Connection, account and capability values are read from the Core projection. "
            "No editable broker control is available in this version; it will only be exposed "
            "when a versioned IPC command can confirm the effective value."
        ),
        "es": (
            "Los valores de conexión, cuenta y capacidades se leen de la proyección del Core. "
            "No hay controles editables del corredor en esta versión; solo se mostrarán cuando "
            "un comando IPC versionado pueda confirmar el valor efectivo."
        ),
    },
    "config.iq_option.body": {
        "en": (
            "Connection, account and capability values are read from the Core projection. "
            "No editable broker control is available in this version. Broker credentials never "
            "belong to the product identity service or to this UI."
        ),
        "es": (
            "Los valores de conexión, cuenta y capacidades se leen de la proyección del Core. "
            "No hay controles editables del corredor en esta versión. Las credenciales nunca "
            "pertenecen al servicio de identidad del producto ni a esta interfaz."
        ),
    },
    "settings.intro": {
        "en": (
            "Settings are separated from live monitoring. Values that affect finance are shown "
            "as Core-managed projections and cannot be changed by the UI alone."
        ),
        "es": (
            "La configuración está separada del monitoreo operativo. Los valores con impacto "
            "financiero se muestran como proyecciones administradas por el Core y la interfaz no "
            "puede cambiarlos por sí sola."
        ),
    },
    "settings.application.tab": {"en": "Application", "es": "Aplicación"},
    "settings.application.title": {
        "en": "Application preferences",
        "es": "Preferencias de la aplicación",
    },
    "settings.application.body": {
        "en": (
            "Language is changed in the header. The UI reads bounded projections and does not "
            "open broker connections or financial databases."
        ),
        "es": (
            "El idioma se cambia en el encabezado. La interfaz lee proyecciones limitadas y no "
            "abre conexiones de corredor ni bases de datos financieras."
        ),
    },
    "settings.application.scope": {
        "en": "Scope: this Windows user and this UI session",
        "es": "Alcance: este usuario de Windows y esta sesión de interfaz",
    },
    "settings.application.effective": {
        "en": "Effective now: language selector in the persistent header",
        "es": "Efectivo ahora: selector de idioma en el encabezado persistente",
    },
    "settings.risk.tab": {"en": "Risk & safety", "es": "Riesgo y seguridad"},
    "settings.risk.title": {
        "en": "Global risk is Core-managed",
        "es": "El riesgo global es administrado por el Core",
    },
    "settings.risk.body": {
        "en": (
            "Limits apply across brokers. Safe Stop blocks new entries but keeps open, UNKNOWN "
            "and reconciling orders under monitoring."
        ),
        "es": (
            "Los límites se aplican entre corredores. Safe Stop bloquea nuevas entradas, pero "
            "mantiene bajo seguimiento las órdenes abiertas, UNKNOWN y en reconciliación."
        ),
    },
    "settings.risk.scope": {
        "en": "Scope: global portfolio, broker accounts and active exposure",
        "es": "Alcance: cartera global, cuentas de corredores y exposición activa",
    },
    "settings.risk.effective": {
        "en": "Waiting for the effective risk projection from the Core",
        "es": "Esperando la proyección de riesgo efectiva del Core",
    },
    "settings.risk.projected": {
        "en": "Effective now: {active} of {limit} · state {state}",
        "es": "Efectivo ahora: {active} de {limit} · estado {state}",
    },
    "settings.strategies.tab": {"en": "Strategies", "es": "Estrategias"},
    "settings.strategies.title": {
        "en": "Versioned strategy configuration",
        "es": "Configuración versionada de estrategias",
    },
    "settings.strategies.body": {
        "en": (
            "Strategy changes require a compatible manifest and a new immutable configuration. "
            "Runtime, Arbiter and Allocator always run before the Risk Ledger."
        ),
        "es": (
            "Los cambios de estrategia requieren un manifiesto compatible y una nueva "
            "configuración inmutable. Runtime, Arbiter y Allocator siempre se ejecutan antes del "
            "Risk Ledger."
        ),
    },
    "settings.strategies.scope": {
        "en": "Scope: strategy version, broker, account, product, asset and timeframe",
        "es": "Alcance: versión, corredor, cuenta, producto, activo y timeframe",
    },
    "settings.strategies.effective": {
        "en": "Read-only in this version; no unconfirmed parameter control is displayed",
        "es": "Solo lectura en esta versión; no se muestra ningún control sin confirmación",
    },
    "settings.support.tab": {"en": "Diagnostics", "es": "Diagnóstico"},
    "settings.support.title": {
        "en": "Diagnostics and support",
        "es": "Diagnóstico y soporte",
    },
    "settings.support.body": {
        "en": (
            "Export creates a local redacted bundle. Financial databases, vaults and session or "
            "broker credentials are excluded."
        ),
        "es": (
            "La exportación crea un paquete local redactado. Se excluyen bases financieras, "
            "vaults y credenciales de sesión o de corredores."
        ),
    },
    "settings.support.scope": {
        "en": "Scope: local support evidence only",
        "es": "Alcance: solo evidencia local de soporte",
    },
    "settings.support.effective": {
        "en": "Use Export Diagnostics in the persistent action bar",
        "es": "Use Exportar Diagnóstico en la barra de acciones persistente",
    },
    "kpi.pnl_detail": {
        "en": "Realized session performance",
        "es": "Rendimiento realizado de la sesión",
    },
    "error.safe_stop_title": {"en": "Safe Stop", "es": "Safe Stop"},
    "error.safe_stop_message": {
        "en": "Could not stop new entries: {error}",
        "es": "No se pudieron detener las nuevas entradas: {error}",
    },
    "error.resume_title": {"en": "Resume entries", "es": "Reanudar entradas"},
    "error.resume_message": {
        "en": "Could not resume entries: {error}",
        "es": "No se pudieron reanudar las entradas: {error}",
    },
    # Health Gates
    "gates.title": {
        "en": "Health Gates Monitor",
        "es": "Monitor de Puertas de Salud",
    },
    "gates.open": {
        "en": "OPEN / OK",
        "es": "ABIERTO / OK",
    },
    "gates.blocked": {
        "en": "BLOCKED",
        "es": "BLOQUEADO",
    },
    # Orders Table
    "orders.title": {
        "en": "Order Book & Contract Activity",
        "es": "Libro de Órdenes y Actividad",
    },
    "orders.empty": {
        "en": "No persisted orders in this session.",
        "es": "No hay órdenes persistidas en esta sesión.",
    },
    "orders.col.id": {
        "en": "Order ID",
        "es": "ID Orden",
    },
    "orders.col.broker": {
        "en": "Broker",
        "es": "Corredor",
    },
    "orders.col.symbol": {
        "en": "Asset / Symbol",
        "es": "Activo / Símbolo",
    },
    "orders.col.direction": {
        "en": "Direction",
        "es": "Dirección",
    },
    "orders.col.amount": {
        "en": "Amount / Stake",
        "es": "Monto / Stake",
    },
    "orders.col.state": {
        "en": "State",
        "es": "Estado",
    },
    "orders.col.time": {
        "en": "Created (UTC)",
        "es": "Creado (UTC)",
    },
    # Action Bar & Buttons
    "btn.safe_stop": {
        "en": "STOP NEW ENTRIES (SAFE STOP)",
        "es": "DETENER NUEVAS ENTRADAS (SAFE STOP)",
    },
    "btn.resume": {
        "en": "Resume Entries",
        "es": "Reanudar Entradas",
    },
    "btn.diagnostic": {
        "en": "Export Diagnostics (.zip)",
        "es": "Exportar Diagnóstico (.zip)",
    },
    "btn.safe_close": {
        "en": "Safe Close",
        "es": "Cerrar Seguro",
    },
    # Lifecycle & System States
    "state.READY": {
        "en": "READY / OPERATIONAL",
        "es": "LISTO / OPERATIVO",
    },
    "state.DEGRADED": {
        "en": "DEGRADED",
        "es": "DEGRADADO",
    },
    "state.SAFE_STOPPED": {
        "en": "SAFE STOP ACTIVE",
        "es": "PARADA SEGURA ACTIVA",
    },
    "state.RECONCILING": {
        "en": "RECONCILING",
        "es": "RECONCILIANDO",
    },
    "state.RISK_LOCKED": {
        "en": "RISK LOCKED",
        "es": "BLOQUEO DE RIESGO",
    },
    "state.UNKNOWN": {
        "en": "UNKNOWN",
        "es": "DESCONOCIDO",
    },
    # Risk States
    "risk.NORMAL": {
        "en": "NORMAL",
        "es": "NORMAL",
    },
    "risk.WARN_DRAWDOWN": {
        "en": "DRAWDOWN WARNING",
        "es": "ALERTA DRAWDOWN",
    },
    "risk.HALTED_MAX_LOSS": {
        "en": "HALTED (MAX LOSS)",
        "es": "DETENIDO (MAX PÉRDIDA)",
    },
    "risk.HALTED_CONSECUTIVE_LOSS": {
        "en": "HALTED (CONSECUTIVE LOSSES)",
        "es": "DETENIDO (PÉRDIDAS CONSECUTIVAS)",
    },
    "risk.HALTED_MAX_EXPOSURE": {
        "en": "HALTED (MAX EXPOSURE)",
        "es": "DETENIDO (MAX EXPOSICIÓN)",
    },
    # Diagnostic Modal
    "diag.title": {
        "en": "Diagnostic Bundle Generated",
        "es": "Paquete de Diagnóstico Generado",
    },
    "diag.message": {
        "en": (
            "Redacted diagnostic archive generated successfully:\n\n"
            "Path: {path}\nSize: {size} bytes\nSHA-256: {sha256}"
        ),
        "es": (
            "Paquete de diagnóstico redigido generado con éxito:\n\n"
            "Ruta: {path}\nTamaño: {size} bytes\nSHA-256: {sha256}"
        ),
    },
    "diag.error_title": {
        "en": "Diagnostic Generation Failed",
        "es": "Fallo al Generar Diagnóstico",
    },
    "diag.error_message": {
        "en": "Could not generate diagnostic package: {error}",
        "es": "No se pudo generar el paquete de diagnóstico: {error}",
    },
}


class I18nManager:
    DEFAULT_LANGUAGE: ClassVar[str] = "es"
    SUPPORTED_LANGUAGES: ClassVar[tuple[str, str]] = ("es", "en")

    _current_lang: str = "es"
    _listeners: ClassVar[list[Callable[[str], None]]] = []

    @classmethod
    def set_language(cls, lang: str) -> None:
        normalized = lang.strip().lower()
        if normalized not in cls.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {lang}. Supported: {cls.SUPPORTED_LANGUAGES}")
        if cls._current_lang != normalized:
            cls._current_lang = normalized
            for listener in cls._listeners:
                with contextlib.suppress(Exception):
                    listener(normalized)

    @classmethod
    def get_language(cls) -> str:
        return cls._current_lang

    @classmethod
    def subscribe(cls, listener: Callable[[str], None]) -> None:
        if listener not in cls._listeners:
            cls._listeners.append(listener)

    @classmethod
    def unsubscribe(cls, listener: Callable[[str], None]) -> None:
        if listener in cls._listeners:
            cls._listeners.remove(listener)

    @classmethod
    def t(cls, key: str, **kwargs: object) -> str:
        entry = TRANSLATIONS.get(key)
        if entry is None:
            return key
        text = entry.get(cls._current_lang) or entry.get(cls.DEFAULT_LANGUAGE) or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text


def t(key: str, **kwargs: object) -> str:
    return I18nManager.t(key, **kwargs)
