"""Unit tests for email providers and Spanish templates."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import httpx
import pytest

from apps.license_server.mail import (
    ConsoleMailProvider,
    ResendMailProvider,
    mask_email,
    render_email_content,
)


def test_mask_email() -> None:
    assert mask_email("juan@example.com") == "ju***@example.com"
    assert mask_email("a@b.com") == "a***@b.com"
    assert mask_email("invalid") == "***"


def test_render_email_content_spanish_no_broken_html() -> None:
    code = "739201"
    expires = 5
    support = "https://t.me/tradinglab_support"

    subject, text, html = render_email_content(code, expires, support)

    # Subject check
    assert subject == "Tu código de acceso a Trading Lab: 739201"

    # Text check
    assert "739201" in text
    assert "5 minutos" in text
    assert support in text
    assert "Hola." in text

    # HTML check
    assert "<!DOCTYPE html>" in html
    assert '<html lang="es">' in html
    assert "</html>" in html
    assert "<body>" in html or "<body " in html
    assert "</body>" in html
    assert "739201" in html
    assert support in html


def test_console_mail_provider_logs_masked_email(caplog: pytest.LogCaptureFixture) -> None:
    provider = ConsoleMailProvider()
    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        provider.send_otp("juan.perez@example.com", "654321", 5)

    assert "OTP for ju***@example.com: 654321" in caplog.text


def test_resend_mail_provider_error_handling() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=MagicMock(),
        response=MagicMock(status_code=401),
    )
    mock_client.post.return_value = mock_response

    provider = ResendMailProvider(
        api_key="re_invalid",
        mail_from="Trading Lab <test@tradinglab.app>",
        support_contact_url="https://t.me/support",
        http_client=mock_client,
    )

    with pytest.raises(RuntimeError, match="Email delivery failed"):
        provider.send_otp("test@example.com", "123456", 5, challenge_id="ch-1")
