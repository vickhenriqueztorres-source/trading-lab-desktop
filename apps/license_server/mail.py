"""Email delivery providers and templates for Trading Lab License Server."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from apps.license_server.settings import Settings

logger = logging.getLogger("license_server.mail")


def mask_email(email: str) -> str:
    """Mask email for privacy in logs (e.g. ju***@example.com)."""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    masked_local = f"{local[0]}***" if len(local) <= 2 else f"{local[:2]}***"
    return f"{masked_local}@{domain}"


def render_email_content(code: str, expires_minutes: int, support_url: str) -> tuple[str, str, str]:
    """Render subject, plain text and HTML in Spanish."""
    subject = f"Tu código de acceso a Trading Lab: {code}"

    text_body = (
        f"Hola.\n\n"
        f"Tu código de acceso es {code}. Vence en {expires_minutes} minutos.\n\n"
        f"Si no fuiste tú, ignora este mensaje.\n\n"
        f"— Trading Lab\n"
        f"Soporte: {support_url}\n"
    )

    html_body = (
        '<!DOCTYPE html>\n<html lang="es">\n<head><meta charset="utf-8"></head>\n'
        '<body style="font-family: sans-serif; color: #1e293b; background: #0f172a; '
        'padding: 24px;">\n'
        '  <div style="max-width: 480px; margin: 0 auto; background: #ffffff; '
        'padding: 32px; border-radius: 8px;">\n'
        '    <h2 style="margin-top: 0; color: #0f172a;">Trading Lab</h2>\n'
        "    <p>Hola,</p>\n"
        "    <p>Tu código de acceso es:</p>\n"
        '    <div style="font-size: 32px; font-weight: bold; letter-spacing: 6px; padding: 16px; '
        'background: #f1f5f9; text-align: center; border-radius: 6px; margin: 20px 0;">\n'
        f"      {code}\n"
        "    </div>\n"
        f'    <p style="color: #64748b; font-size: 14px;">Vence en {expires_minutes} minutos. '
        "Si no fuiste tú, ignora este mensaje.</p>\n"
        '    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />\n'
        f'    <p style="color: #94a3b8; font-size: 12px; margin: 0;">Trading Lab &bull; '
        f'<a href="{support_url}" style="color: #2563eb; text-decoration: none;">Soporte</a></p>\n'
        "  </div>\n"
        "</body>\n</html>"
    )

    return subject, text_body, html_body


class MailProvider(Protocol):
    """Protocol for sending transactional emails."""

    def send_otp(
        self,
        to_email: str,
        code: str,
        expires_minutes: int,
        *,
        challenge_id: str | None = None,
    ) -> None: ...


class ConsoleMailProvider:
    """Development mail provider logging masked OTP code."""

    def __init__(self, support_contact_url: str = "https://t.me/tradinglab_support") -> None:
        self._support_contact_url = support_contact_url

    def send_otp(
        self,
        to_email: str,
        code: str,
        expires_minutes: int,
        *,
        challenge_id: str | None = None,
    ) -> None:
        masked = mask_email(to_email)
        logger.info("OTP for %s: %s", masked, code)
        print("\n=======================================================", flush=True)
        print(f" [TRADING LAB DEV OTP] CÓDIGO DE ACESSO: >>> {code} <<<", flush=True)
        print(f" Destinatário: {to_email} (Expira em {expires_minutes} minutos)", flush=True)
        print("=======================================================\n", flush=True)
        try:
            with open("ULTIMO_CODIGO_OTP.txt", "w", encoding="utf-8") as f:
                f.write(
                    f"=======================================================\n"
                    f" CÓDIGO DE ACESSO (OTP): {code}\n"
                    f" Destinatário: {to_email}\n"
                    f" Expira em: {expires_minutes} minutos\n"
                    f"=======================================================\n"
                )
        except Exception:
            pass


class ResendMailProvider:
    """Production mail provider integrating with Resend API."""

    def __init__(
        self,
        api_key: str,
        mail_from: str,
        support_contact_url: str,
        *,
        timeout_seconds: float = 8.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._mail_from = mail_from
        self._support_contact_url = support_contact_url
        self._timeout = timeout_seconds
        self._client = http_client

    def send_otp(
        self,
        to_email: str,
        code: str,
        expires_minutes: int,
        *,
        challenge_id: str | None = None,
    ) -> None:
        subject, text_body, html_body = render_email_content(
            code,
            expires_minutes,
            self._support_contact_url,
        )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if challenge_id:
            headers["Idempotency-Key"] = challenge_id

        payload = {
            "from": self._mail_from,
            "to": [to_email],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        }

        try:
            if self._client is not None:
                response = self._client.post(
                    "https://api.resend.com/emails",
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        "https://api.resend.com/emails",
                        headers=headers,
                        json=payload,
                    )
            response.raise_for_status()
        except Exception as exc:
            logger.error("Failed to send OTP email via Resend to %s: %s", mask_email(to_email), exc)
            raise RuntimeError("Email delivery failed") from exc


def get_mail_provider(settings: Settings) -> MailProvider:
    """Factory selecting the mail provider based on settings."""
    if settings.mail_provider == "resend" and settings.resend_api_key:
        return ResendMailProvider(
            api_key=settings.resend_api_key,
            mail_from=settings.mail_from,
            support_contact_url=settings.support_contact_url,
        )
    return ConsoleMailProvider(support_contact_url=settings.support_contact_url)
