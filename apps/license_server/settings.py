"""Settings and environment variable loader for Trading Lab License Server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    license_signing_key_pem: str | None
    license_signing_key_id: str
    admin_email: str
    admin_session_secret: str
    mail_provider: str
    resend_api_key: str | None
    mail_from: str
    public_base_url: str
    support_contact_url: str
    renew_url: str
    hotmart_hottok: str | None
    download_url: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.environ.get("ENVIRONMENT", "development").strip().lower()
        is_production = environment in {"production", "prod"}

        database_url = os.environ.get("DATABASE_URL", "").strip()
        if is_production and not database_url:
            raise ValueError("DATABASE_URL is required in production environment")

        signing_key_pem = os.environ.get("LICENSE_SIGNING_KEY_PEM")
        if signing_key_pem is not None:
            signing_key_pem = signing_key_pem.strip() or None

        signing_key_id = os.environ.get("LICENSE_SIGNING_KEY_ID", "tl-2026-09").strip()
        if is_production and (not signing_key_pem or not signing_key_id):
            msg = "LICENSE_SIGNING_KEY_PEM and LICENSE_SIGNING_KEY_ID are required in production"
            raise ValueError(msg)

        admin_email = os.environ.get("ADMIN_EMAIL", "admin@tradinglab.app").strip().lower()
        if is_production and not admin_email:
            raise ValueError("ADMIN_EMAIL is required")

        admin_session_secret = os.environ.get("ADMIN_SESSION_SECRET", "").strip()
        if is_production:
            if not admin_session_secret or len(admin_session_secret.encode("utf-8")) < 32:
                msg = "ADMIN_SESSION_SECRET must be at least 32 characters in production"
                raise ValueError(msg)
        elif not admin_session_secret:
            admin_session_secret = "development-only-session-secret-must-be-32-chars-long"

        mail_provider = os.environ.get("MAIL_PROVIDER", "console").strip().lower()
        if mail_provider not in {"resend", "console"}:
            raise ValueError("MAIL_PROVIDER must be 'resend' or 'console'")

        resend_api_key = os.environ.get("RESEND_API_KEY", "").strip() or None
        if mail_provider == "resend" and not resend_api_key:
            raise ValueError("RESEND_API_KEY is required when MAIL_PROVIDER is 'resend'")

        mail_from = os.environ.get("MAIL_FROM", "Trading Lab <acceso@tradinglab.app>").strip()
        public_base_url = os.environ.get(
            "PUBLIC_BASE_URL", "https://licencias.tradinglab.app"
        ).strip()
        support_contact_url = os.environ.get(
            "SUPPORT_CONTACT_URL", "https://t.me/tradinglab_support"
        ).strip()
        renew_url = os.environ.get("RENEW_URL", "https://tradinglab.app/renew").strip()
        hotmart_hottok = os.environ.get("HOTMART_HOTTOK", "").strip() or None
        download_url = os.environ.get("DOWNLOAD_URL", "").strip() or None

        return cls(
            environment=environment,
            database_url=database_url,
            license_signing_key_pem=signing_key_pem,
            license_signing_key_id=signing_key_id,
            admin_email=admin_email,
            admin_session_secret=admin_session_secret,
            mail_provider=mail_provider,
            resend_api_key=resend_api_key,
            mail_from=mail_from,
            public_base_url=public_base_url,
            support_contact_url=support_contact_url,
            renew_url=renew_url,
            hotmart_hottok=hotmart_hottok,
            download_url=download_url,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
