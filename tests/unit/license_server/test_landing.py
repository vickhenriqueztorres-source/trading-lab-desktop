"""Tests for public Spanish landing page, security headers, and download links."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from apps.license_server.main import create_app
from apps.license_server.settings import Settings


def test_landing_page_renders_in_spanish_with_required_elements(app_client: TestClient) -> None:
    """GET / should render the landing page in Spanish with title, blocks, and risk warning."""
    response = app_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    html = response.text
    # 1. HTML lang attribute
    assert '<html lang="es">' in html

    # 2. Main title
    assert "Trading Lab — bots para opciones binarias en Deriv e IQ Option" in html

    # 3. Required 3 content blocks
    assert "Cómo funciona" in html
    assert "Requisitos" in html
    assert "Cómo acceder" in html

    # 4. Mandatory action buttons
    assert "Comprar acceso" in html
    assert "Soporte" in html

    # 5. Risk warning
    assert (
        "Operar opciones binarias implica riesgo de pérdida total del capital. "
        "Trading Lab es una herramienta de automatización y no garantiza resultados."
    ) in html

    # 6. Strictly NO JavaScript tags
    assert "<script" not in html.lower()


def test_landing_download_button_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Download button should only be rendered when download_url is configured."""
    # Scenario A: download_url is None
    settings_no_download = Settings(
        environment="test",
        database_url="",
        license_signing_key_pem=None,
        license_signing_key_id="tl-test-key",
        admin_email="admin@test.invalid",
        admin_session_secret="test-session-secret-at-least-32-chars-long",
        mail_provider="console",
        resend_api_key=None,
        mail_from="Trading Lab <test@tradinglab.app>",
        public_base_url="https://licencias.tradinglab.app",
        support_contact_url="https://t.me/test_support",
        renew_url="https://tradinglab.app/renew",
        hotmart_hottok=None,
        download_url=None,
    )
    client_a = TestClient(create_app(settings_no_download))
    res_a = client_a.get("/")
    assert res_a.status_code == 200
    assert "Descargar para Windows" not in res_a.text

    # Scenario B: download_url is present
    settings_with_download = Settings(
        environment="test",
        database_url="",
        license_signing_key_pem=None,
        license_signing_key_id="tl-test-key",
        admin_email="admin@test.invalid",
        admin_session_secret="test-session-secret-at-least-32-chars-long",
        mail_provider="console",
        resend_api_key=None,
        mail_from="Trading Lab <test@tradinglab.app>",
        public_base_url="https://licencias.tradinglab.app",
        support_contact_url="https://t.me/test_support",
        renew_url="https://tradinglab.app/renew",
        hotmart_hottok=None,
        download_url="https://cdn.tradinglab.app/releases/TradingLab-Setup-1.9.11.exe",
    )
    client_b = TestClient(create_app(settings_with_download))
    res_b = client_b.get("/")
    assert res_b.status_code == 200
    assert "Descargar para Windows" in res_b.text
    assert "https://cdn.tradinglab.app/releases/TradingLab-Setup-1.9.11.exe" in res_b.text


def test_security_headers_present_on_landing_page(app_client: TestClient) -> None:
    """Verify security headers on GET /."""
    response = app_client.get("/")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Strict-Transport-Security") == "max-age=63072000"
    assert response.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"


def test_security_headers_present_on_api_endpoints(app_client: TestClient) -> None:
    """Verify security headers are applied globally across all endpoints."""
    # Check on /.well-known/lease-keys
    res_wellknown = app_client.get("/.well-known/lease-keys")
    assert res_wellknown.headers.get("X-Content-Type-Options") == "nosniff"
    assert res_wellknown.headers.get("X-Frame-Options") == "DENY"
    assert res_wellknown.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert res_wellknown.headers.get("Strict-Transport-Security") == "max-age=63072000"
    assert (
        res_wellknown.headers.get("Permissions-Policy")
        == "camera=(), microphone=(), geolocation=()"
    )
