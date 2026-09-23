"""Testes de validação dos defaults de produção e proteção contra simulação em release."""

from __future__ import annotations

import pytest

from apps.launcher.build_defaults import (
    AUTH_BASE_URL_DEFAULT,
    FORCE_SIMULATION_DEFAULT,
    SUPPORT_CONTACT_URL_DEFAULT,
    SUPPORT_RENEW_URL_DEFAULT,
    get_auth_base_url,
    get_support_contact_url,
    get_support_renew_url,
)
from apps.launcher.cli import build_parser


def test_production_build_defaults_are_configured() -> None:
    """Verifica que as URLs padrão de produção estão configuradas e usam HTTPS."""
    assert AUTH_BASE_URL_DEFAULT.startswith("https://")
    assert "licencias" in AUTH_BASE_URL_DEFAULT
    assert SUPPORT_RENEW_URL_DEFAULT.startswith("https://")
    assert SUPPORT_CONTACT_URL_DEFAULT.startswith("https://")


def test_force_simulation_is_never_production_default() -> None:
    """Modo simulação NUNCA deve ser o padrão de release."""
    assert FORCE_SIMULATION_DEFAULT is False

    arguments = build_parser().parse_args([])
    assert arguments.force_auth_simulation is False
    assert arguments.auth_base_url == AUTH_BASE_URL_DEFAULT


def test_force_simulation_accessible_only_by_explicit_flag() -> None:
    """Modo simulação é acessível apenas com a flag explícita de desenvolvedor."""
    arguments = build_parser().parse_args(["--force-auth-simulation"])
    assert arguments.force_auth_simulation is True


def test_auth_base_url_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A variável de ambiente TRADING_LAB_AUTH_BASE_URL tem precedência sobre o default."""
    staging_url = "https://staging-licencias.tradinglab.app"
    monkeypatch.setenv("TRADING_LAB_AUTH_BASE_URL", staging_url)

    assert get_auth_base_url() == staging_url
    arguments = build_parser().parse_args([])
    assert arguments.auth_base_url == staging_url


def test_support_urls_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variáveis de ambiente de suporte têm precedência sobre os defaults."""
    monkeypatch.setenv("TRADING_LAB_RENEW_URL", "https://custom.renew.url")
    monkeypatch.setenv("TRADING_LAB_SUPPORT_URL", "https://custom.support.url")

    assert get_support_renew_url() == "https://custom.renew.url"
    assert get_support_contact_url() == "https://custom.support.url"
