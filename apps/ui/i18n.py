from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import ClassVar

TRANSLATIONS: dict[str, dict[str, str]] = {
    "DIGIT_STRATEGY_TITLE": {
        "en": "Shared Digit Edge risk parameters",
        "es": "Parámetros de riesgo compartidos Digit Edge",
    },
    "STAKE_LABEL": {"en": "Stake Amount (USD)", "es": "Monto por Entrada (USD)"},
    "STOP_LOSS_LABEL": {"en": "Daily Stop Loss", "es": "Stop Loss Diario"},
    "TAKE_PROFIT_LABEL": {
        "en": "Daily Take Profit",
        "es": "Meta de Ganancia (Take Profit)",
    },
    "CONSECUTIVE_LOSS_LABEL": {
        "en": "Max Consecutive Losses",
        "es": "Pérdidas Consecutivas Máx.",
    },
    "COOLDOWN_LABEL": {"en": "Post-Loss Cooldown", "es": "Pausa Post-Pérdida"},
    "CONFIDENCE_LABEL": {
        "en": "Conservative entry filter",
        "es": "Filtro conservador de entrada",
    },
    "AUTO_SYMBOL_LABEL": {
        "en": "Automatic asset selection (Demo)",
        "es": "Selección automática de activo (Demo)",
    },
    "AUTO_SYMBOL_HELP": {
        "en": (
            "The manual asset remains the fallback. Automatic switching is disabled "
            "for real accounts."
        ),
        "es": (
            "El activo manual queda como reserva. El cambio automático está "
            "desactivado para cuentas reales."
        ),
    },
    "APPLY_CONFIG_BTN": {"en": "Apply Parameters", "es": "Aplicar Parámetros"},
    "DIGIT_SYMBOL_LABEL": {"en": "Deriv Synthetic Index", "es": "Índice Sintético Deriv"},
    "DIGIT_CONFIDENCE_DISCLAIMER": {
        "en": "Statistical threshold; it is not a profit forecast or result guarantee.",
        "es": "Umbral estadístico; no es una previsión de lucro ni garantía de resultado.",
    },
    "DIGIT_CONFIG_VALID": {"en": "Configuration is valid.", "es": "Configuración válida."},
    "DIGIT_CONFIG_INVALID": {
        "en": "Review the monetary fields; minimum stake is USD 0.35.",
        "es": "Revise los campos monetarios; el stake mínimo es USD 0,35.",
    },
    "DIGIT_CONFIG_APPLIED": {
        "en": "Configuration applied by the Core",
        "es": "Configuración aplicada por el Core",
    },
    "DIGIT_CONFIG_REJECTED": {
        "en": "Core rejected the configuration: {reason}",
        "es": "El Core rechazó la configuración: {reason}",
    },
    "DIGIT_RISK_PROJECTION": {
        "en": "Configured take-profit / stop-loss ratio: {ratio}",
        "es": "Relación configurada take-profit / stop-loss: {ratio}",
    },
    "DIGIT_RISK_PROJECTION_UNAVAILABLE": {
        "en": "Risk/return projection unavailable until all fields are valid.",
        "es": "Proyección riesgo/retorno no disponible hasta validar todos los campos.",
    },
    "MARTINGALE_ENABLED_LABEL": {
        "en": "Martingale",
        "es": "Martingale",
    },
    "MARTINGALE_MULTIPLIER_LABEL": {
        "en": "Multiplier",
        "es": "Multiplicador",
    },
    "MARTINGALE_STEPS_LABEL": {
        "en": "Recovery steps",
        "es": "Pasos de recuperación",
    },
    "MARTINGALE_MAX_STAKE_LABEL": {
        "en": "Absolute stake cap (USD)",
        "es": "Tope absoluto de stake (USD)",
    },
    "MARTINGALE_DISABLED_STATUS": {
        "en": "Bounded Martingale OFF · fixed stake remains active.",
        "es": "Martingale Delimitado DESACTIVADO · stake fija activa.",
    },
    "MARTINGALE_PROJECTION": {
        "en": "Bounded sequence: {sequence} · maximum projected sequence loss USD {loss}",
        "es": "Secuencia delimitada: {sequence} · pérdida máxima proyectada USD {loss}",
    },
    "MARTINGALE_PROJECTION_UNAVAILABLE": {
        "en": "Martingale projection unavailable until all caps are valid.",
        "es": "Proyección de Martingale no disponible hasta validar todos los topes.",
    },
    "DIGIT_COOLDOWN_ACTIVE": {
        "en": "Post-loss pause active: {seconds} s remaining",
        "es": "Pausa post-pérdida activa: restan {seconds} s",
    },
    "DIGIT_COOLDOWN_READY": {
        "en": "Post-loss pause: ready",
        "es": "Pausa post-pérdida: lista",
    },
    "DIGIT_FREQUENCY_TITLE": {
        "en": "Live digit frequency (0–9)",
        "es": "Frecuencia de dígitos en vivo (0–9)",
    },
    "DIGIT_FREQUENCY_WAITING": {
        "en": "Waiting for the first live ticks…",
        "es": "Esperando los primeros ticks en vivo…",
    },
    "DIGIT_FREQUENCY_SUMMARY": {
        "en": "{symbol} · {ticks} ticks · transport latency {latency} µs",
        "es": "{symbol} · {ticks} ticks · latencia de transporte {latency} µs",
    },
    "DIGIT_FREQUENCY_DISCLAIMER": {
        "en": (
            "Amber marks the most frequent observed digit and cyan the least frequent. "
            "This historical window is not a prediction or a profit guarantee."
        ),
        "es": (
            "Ámbar marca el dígito observado más frecuente y cian el menos frecuente. "
            "Esta ventana histórica no es una predicción ni una garantía de lucro."
        ),
    },
    "deriv.radar.title": {
        "en": "Multi-asset Shadow radar",
        "es": "Radar Shadow multiactivo",
    },
    "deriv.radar.subtitle": {
        "en": (
            "Independent buffers rank candidates and Demo automation may select the "
            "strongest eligible asset."
        ),
        "es": (
            "Buffers independientes clasifican candidatos y la automatización Demo puede "
            "seleccionar el activo elegible más fuerte."
        ),
    },
    "deriv.radar.notice": {
        "en": (
            "Demo only: a candidate is executed only after the statistical edge and recent "
            "financial result filters approve it. Otherwise the bot abstains."
        ),
        "es": (
            "Solo Demo: un candidato se ejecuta únicamente si los filtros de ventaja estadística "
            "y resultado financiero reciente lo aprueban. En caso contrario, el bot se abstiene."
        ),
    },
    "deriv.radar.abstain": {
        "en": "NO ELIGIBLE ASSET — WAITING",
        "es": "SIN ACTIVO ELEGIBLE — ESPERANDO",
    },
    "deriv.radar.candidate": {
        "en": "SHADOW CANDIDATE · {symbol}",
        "es": "CANDIDATO SHADOW · {symbol}",
    },
    "deriv.radar.rank": {"en": "Rank", "es": "Posición"},
    "deriv.radar.asset": {"en": "Asset", "es": "Activo"},
    "deriv.radar.state": {"en": "State", "es": "Estado"},
    "deriv.radar.best_signal": {"en": "Best Shadow signal", "es": "Mejor señal Shadow"},
    "deriv.radar.margin": {"en": "Stat. margin", "es": "Margen estad."},
    "deriv.radar.warmup": {"en": "Warm-up", "es": "Calentamiento"},
    "deriv.radar.state.candidate": {"en": "CANDIDATE", "es": "CANDIDATO"},
    "deriv.radar.state.monitoring": {"en": "MONITORING", "es": "MONITOREANDO"},
    "deriv.radar.state.warming": {"en": "WARMING", "es": "CALENTANDO"},
    "deriv.radar.state.blocked": {"en": "BLOCKED", "es": "BLOQUEADO"},
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
    "mode.REAL": {
        "en": "REAL MONEY",
        "es": "DINERO REAL",
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
    "config.real_mode_available": {
        "en": (
            "Real mode is available only when the user explicitly selects an API-confirmed "
            "real account."
        ),
        "es": (
            "El modo real solo está disponible cuando el usuario selecciona explícitamente una "
            "cuenta real confirmada por la API."
        ),
    },
    "config.real_mode_active": {
        "en": "REAL MONEY MODE ACTIVE. Orders affect the selected account's real balance.",
        "es": "MODO DINERO REAL ACTIVO. Las órdenes afectan el saldo real de la cuenta.",
    },
    "config.deriv.body": {
        "en": (
            "Connect a Deriv Demo or Real account here after the application has opened. The "
            "API token is protected by Windows DPAPI; Real always requires explicit selection "
            "and confirmation."
        ),
        "es": (
            "Conecte una cuenta Deriv Demo o Real aquí después de abrir la aplicación. El token "
            "API se protege con Windows DPAPI; Real siempre exige selección y confirmación "
            "explícitas."
        ),
    },
    "deriv.connect.button": {
        "en": "Connect Deriv account",
        "es": "Conectar cuenta Deriv",
    },
    "deriv.connect.status.ready": {
        "en": "Enter new credentials or reuse credentials already protected by Windows.",
        "es": "Ingrese credenciales nuevas o reutilice las ya protegidas por Windows.",
    },
    "deriv.hub.title": {
        "en": "Deriv Strategy Command Center",
        "es": "Centro de Estrategias Deriv",
    },
    "deriv.hub.body": {
        "en": "One account, multiple isolated strategies, with clear risk and execution control.",
        "es": (
            "Una cuenta, múltiples estrategias aisladas, con control claro de riesgo y ejecución."
        ),
    },
    "deriv.hub.account": {"en": "ACTIVE ACCOUNT", "es": "CUENTA ACTIVA"},
    "deriv.library.title": {"en": "Strategy library", "es": "Biblioteca de estrategias"},
    "deriv.library.body": {
        "en": "All three strategies are monitored. Choose one to inspect its workspace.",
        "es": "Las tres estrategias se monitorean. Seleccione una para inspeccionar su espacio.",
    },
    "deriv.library.note": {
        "en": (
            "The selector changes the visible analysis; it does not disable the other strategies."
        ),
        "es": "El selector cambia el análisis visible; no desactiva las otras estrategias.",
    },
    "deriv.strategy.coming_soon": {"en": "COMING SOON", "es": "PRÓXIMAMENTE"},
    "deriv.strategy.digit_diff": {
        "en": "DIGIT FREQUENCY EDGE\n● ACTIVE",
        "es": "DIGIT FREQUENCY EDGE\n● ACTIVA",
    },
    "deriv.strategy.active_label": {
        "en": "ACTIVE STRATEGY  ·  STATISTICAL FREQUENCY ENGINE",
        "es": "ESTRATEGIA ACTIVA  ·  MOTOR DE FRECUENCIA ESTADÍSTICA",
    },
    "deriv.strategy.digit_diff_title": {
        "en": "Digit Frequency Edge",
        "es": "Digit Frequency Edge",
    },
    "deriv.strategy.digit_diff_body": {
        "en": "Frequency-based one-tick execution with dedicated risk controls and live evidence.",
        "es": "Ejecución de un tick basada en frecuencia, con riesgo dedicado y evidencia en vivo.",
    },
    "deriv.strategy.tabs.overview": {"en": "Strategy overview", "es": "Resumen"},
    "deriv.strategy.tabs.parameters": {"en": "Parameters & risk", "es": "Parámetros y riesgo"},
    "deriv.strategy.tabs.live": {"en": "Live market", "es": "Mercado en vivo"},
    "deriv.strategy.tabs.operations": {"en": "Operations", "es": "Operaciones"},
    "deriv.strategy.metric.state": {"en": "CONNECTION", "es": "CONEXIÓN"},
    "deriv.strategy.metric.account": {"en": "ACCOUNT SCOPE", "es": "CUENTA"},
    "deriv.strategy.metric.automation": {
        "en": "GLOBAL AUTOMATION",
        "es": "AUTOMATIZACIÓN GLOBAL",
    },
    "deriv.strategy.ready": {"en": "ACCOUNT CONNECTED", "es": "CUENTA CONECTADA"},
    "deriv.strategy.waiting": {"en": "WAITING FOR ACCOUNT", "es": "ESPERANDO CUENTA"},
    "deriv.automation.active": {
        "en": "● GLOBAL AUTOMATION ON",
        "es": "● AUTOMATIZACIÓN GLOBAL ACTIVA",
    },
    "deriv.automation.paused": {
        "en": "○ GLOBAL AUTOMATION PAUSED",
        "es": "○ AUTOMATIZACIÓN GLOBAL PAUSADA",
    },
    "deriv.automation.active_short": {"en": "ON", "es": "ACTIVA"},
    "deriv.automation.paused_short": {"en": "PAUSED", "es": "PAUSADA"},
    "deriv.summary.net": {"en": "NET RESULT", "es": "RESULTADO NETO"},
    "deriv.summary.gain": {"en": "GAIN", "es": "GANANCIAS"},
    "deriv.summary.loss": {"en": "LOSS", "es": "PÉRDIDAS"},
    "deriv.summary.win_rate": {"en": "WIN RATE", "es": "TASA DE ACIERTO"},
    "deriv.summary.operations": {
        "en": "Operations: {count}",
        "es": "Operaciones: {count}",
    },
    "deriv.summary.settled": {
        "en": "{count} confirmed settlements",
        "es": "{count} liquidaciones confirmadas",
    },
    "deriv.summary.decided": {
        "en": "{count} decided operations",
        "es": "{count} operaciones decididas",
    },
    "deriv.summary.scope": {
        "en": "Confirmed projection: {count} settlements",
        "es": "Proyección confirmada: {count} liquidaciones",
    },
    "deriv.summary.risk_title": {"en": "Risk management", "es": "Gestión de riesgo"},
    "deriv.summary.exposure": {"en": "GLOBAL EXPOSURE", "es": "EXPOSICIÓN GLOBAL"},
    "deriv.summary.stop_loss": {"en": "DAILY STOP LOSS", "es": "STOP LOSS DIARIO"},
    "deriv.summary.take_profit": {"en": "DAILY TARGET", "es": "META DIARIA"},
    "deriv.summary.consecutive": {
        "en": "CONSECUTIVE LOSSES",
        "es": "PÉRDIDAS CONSECUTIVAS",
    },
    "deriv.summary.cooldown": {"en": "COOLDOWN", "es": "PAUSA DE SEGURIDAD"},
    "deriv.summary.stake": {"en": "STAKE / OPERATION", "es": "STAKE / OPERACIÓN"},
    "deriv.summary.ready": {"en": "READY", "es": "LISTO"},
    "deriv.summary.cooldown_active": {
        "en": "{seconds} s remaining",
        "es": "{seconds} s restantes",
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
    "btn.bot.start": {
        "en": "▶ TURN BOT ON FOR TESTING",
        "es": "▶ ENCENDER BOT PARA PRUEBAS",
    },
    "btn.bot.stop": {
        "en": "■ BOT ON — TURN OFF",
        "es": "■ BOT ENCENDIDO — APAGAR",
    },
    "bot.real.confirm_title": {
        "en": "Real account is read-only",
        "es": "La cuenta Real es solo lectura",
    },
    "bot.real.confirm_message": {
        "en": (
            "Automated entries are enabled only for Demo validation in this release. "
            "Select a Demo account to turn the bot on."
        ),
        "es": (
            "Las entradas automáticas están habilitadas solo para validación Demo en esta "
            "versión. Seleccione una cuenta Demo para encender el bot."
        ),
    },
    "results.title": {
        "en": "Confirmed operation results",
        "es": "Resultados confirmados de operaciones",
    },
    "results.total": {"en": "Settled", "es": "Liquidadas"},
    "results.wins": {"en": "Wins", "es": "Ganadas"},
    "results.losses": {"en": "Losses", "es": "Perdidas"},
    "results.win_rate": {"en": "Observed win rate", "es": "Tasa observada"},
    "results.net": {"en": "Net result", "es": "Resultado neto"},
    "results.scope": {
        "en": (
            "Last {count} confirmed settlements in the bounded projection · breakeven: "
            "{breakeven}. Descriptive history, not a forecast."
        ),
        "es": (
            "Últimas {count} liquidaciones confirmadas en la proyección limitada · empate: "
            "{breakeven}. Historial descriptivo, no previsión."
        ),
    },
    "results.mixed_currency": {"en": "MIXED", "es": "MIXTO"},
    "results.time": {"en": "Time", "es": "Hora"},
    "results.broker": {"en": "Broker", "es": "Corredor"},
    "results.symbol": {"en": "Symbol", "es": "Símbolo"},
    "results.outcome": {"en": "Outcome", "es": "Resultado"},
    "results.pnl": {"en": "Realized P&L", "es": "P&L realizado"},
    "results.won": {"en": "WON", "es": "GANADA"},
    "results.lost": {"en": "LOST", "es": "PERDIDA"},
    "results.even": {"en": "BREAKEVEN", "es": "EMPATE"},
    "results.empty": {
        "en": "No confirmed settlements yet. Turn the bot on in test mode to begin.",
        "es": (
            "Todavía no hay liquidaciones confirmadas. Encienda el bot en modo de prueba "
            "para comenzar."
        ),
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
