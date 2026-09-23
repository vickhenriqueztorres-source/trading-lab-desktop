"""Ed25519 Lease signing key management for Trading Lab License Server."""

from __future__ import annotations

import base64
import logging
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import Connection

from apps.license_server.settings import Settings
from packages.licensing.lease import LeaseSigner

logger = logging.getLogger("license_server.keys")

_CURRENT_KEY_ID: str | None = None
_CURRENT_SIGNER: LeaseSigner | None = None
_CURRENT_PUBKEY_B64: str | None = None
_CURRENT_PEM: str | None = None


def load_signing_key(settings: Settings) -> tuple[str, LeaseSigner, str, str]:
    """Load private Ed25519 key from settings or generate an ephemeral key in development."""
    global _CURRENT_KEY_ID, _CURRENT_SIGNER, _CURRENT_PUBKEY_B64, _CURRENT_PEM

    key_id = settings.license_signing_key_id.strip()
    if not key_id:
        raise ValueError("LICENSE_SIGNING_KEY_ID cannot be empty")

    pem_raw = settings.license_signing_key_pem
    if pem_raw is not None and pem_raw.strip():
        loaded_key = serialization.load_pem_private_key(
            pem_raw.strip().encode("ascii"),
            password=None,
        )
        if not isinstance(loaded_key, Ed25519PrivateKey):
            raise ValueError("LICENSE_SIGNING_KEY_PEM must be an Ed25519 private key")
        private_key = loaded_key
        pem = pem_raw.strip()
    else:
        logger.warning("ephemeral signing key generated for dev")
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")

    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pubkey_b64 = base64.urlsafe_b64encode(pub_bytes).decode("ascii")

    signer = LeaseSigner(key_id=key_id, private_key=private_key)

    _CURRENT_KEY_ID = key_id
    _CURRENT_SIGNER = signer
    _CURRENT_PUBKEY_B64 = pubkey_b64
    _CURRENT_PEM = pem

    return key_id, signer, pubkey_b64, pem


def get_signer() -> LeaseSigner:
    """Return the active LeaseSigner instance."""
    if _CURRENT_SIGNER is None:
        raise RuntimeError("Signing key has not been initialized")
    return _CURRENT_SIGNER


def get_current_key_info() -> tuple[str, str]:
    """Return (key_id, public_key_b64) for the active signing key."""
    if _CURRENT_KEY_ID is None or _CURRENT_PUBKEY_B64 is None:
        raise RuntimeError("Signing key has not been initialized")
    return _CURRENT_KEY_ID, _CURRENT_PUBKEY_B64


def public_keys(conn: Connection[Any] | None = None) -> dict[str, str]:
    """Return active verification keys map (key_id -> pubkey urlsafe b64 with padding)."""
    keys: dict[str, str] = {}
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT key_id, public_key_b64 FROM signing_keys WHERE active = true;")
                for row in cur.fetchall():
                    keys[str(row[0])] = str(row[1])
        except Exception as exc:
            logger.warning("Failed to load signing keys from database: %s", exc)

    if not keys and _CURRENT_KEY_ID is not None and _CURRENT_PUBKEY_B64 is not None:
        keys[_CURRENT_KEY_ID] = _CURRENT_PUBKEY_B64

    return keys


def init_keys_in_db(conn: Connection[Any], settings: Settings) -> None:
    """Ensure current signing key is persisted and active in the database."""
    key_id, _signer, pubkey_b64, pem = load_signing_key(settings)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signing_keys (key_id, private_key_pem, public_key_b64, active)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (key_id) DO UPDATE SET
                private_key_pem = EXCLUDED.private_key_pem,
                public_key_b64 = EXCLUDED.public_key_b64,
                active = true;
            """,
            (key_id, pem, pubkey_b64),
        )
    conn.commit()
    logger.info("Signing key '%s' synchronized to database", key_id)
