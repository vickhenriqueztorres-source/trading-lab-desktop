"""Entitlements and default strategy packs for Trading Lab licenses."""

from __future__ import annotations

PRO_BROKERS: tuple[str, ...] = ("DERIV", "IQ_OPTION")

PRO_STRATEGY_PACKS: tuple[str, ...] = (
    "F1",
    "core",
    "f1:AUDCAD-OTC:M1:00-24:rsi_bollinger",
    "f1:AUDCAD:M1:00-24:rsi_bollinger",
    "f1:AUDUSD:M1:00-24:rsi_bollinger",
    "f1:EURJPY-OTC:M1:00-24:rsi_bollinger",
    "f1:EURJPY:M1:00-24:rsi_bollinger",
    "f1:EURUSD-OTC:M1:00-24:rsi_bollinger",
    "f1:EURUSD:M1:00-24:rsi_bollinger",
    "f1:GBPJPY-OTC:M1:00-24:rsi_bollinger",
    "f1:GBPUSD-OTC:M1:00-24:rsi_bollinger",
    "f1:GBPUSD:M1:00-24:rsi_bollinger",
    "f1:NZDUSD-OTC:M1:00-24:rsi_bollinger",
    "f1:NZDUSD:M1:00-24:rsi_bollinger",
    "f1:USDCHF-OTC:M1:00-24:rsi_bollinger",
    "f1:USDCHF:M1:00-24:rsi_bollinger",
    "f1:USDJPY-OTC:M1:00-24:rsi_bollinger",
    "f1:USDJPY:M1:00-24:rsi_bollinger",
    "iqoption-liquidity-gap",
    "iqoption-pattern-reversal",
    "iqoption-rsi-demo",
    "parity-regime-edge",
    "payout-routed-differs-session",
    "selective-differs-edge",
    "strategy-test",
    "tail-probability-edge",
)
