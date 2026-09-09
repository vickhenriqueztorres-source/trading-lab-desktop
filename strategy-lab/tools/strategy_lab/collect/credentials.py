"""Lab-only credentials: OS keyring / explicit VPS environment (R-COL-1, I-8)."""

from __future__ import annotations

import getpass
import importlib
import os
import sys
import warnings
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr

WINDOWS_TARGET = "StrategyLab/IQOption/collection"
KEYRING_SERVICE = WINDOWS_TARGET
VPS_ENV = "vps"


class Credentials(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    username: SecretStr
    password: SecretStr


def load_credentials() -> Credentials:
    """Read only the Lab's dedicated credential. No main-app vault or file fallback."""
    if os.environ.get("STRATEGY_LAB_ENV") == VPS_ENV:
        return _vps_environment_credentials()
    return _keyring_credentials()


def store_keyring_credentials(username: str, password: str) -> None:
    """Persist the dedicated collection credential in the OS keyring.

    Values are accepted only in memory.  Callers must obtain them through an
    interactive prompt rather than command-line arguments, environment files or
    application state shared with the Desktop Bot (R-COL-1, I-8, I-14).
    """
    candidate = Credentials(
        username=SecretStr(username.strip()),
        password=SecretStr(password),
    )
    safe_username = candidate.username.get_secret_value()
    safe_password = candidate.password.get_secret_value()
    if not safe_username or not safe_password:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_INVALID")
    try:
        keyring = _load_keyring_module()
        keyring.set_password(KEYRING_SERVICE, safe_username, safe_password)
        stored = keyring.get_password(KEYRING_SERVICE, safe_username)
    except Exception:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_STORE_FAILED") from None
    if stored != safe_password:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_STORE_FAILED")


def prompt_and_store_credentials(
    *,
    username_reader: Callable[[str], str] = input,
    password_reader: Callable[[str], str] | None = None,
) -> None:
    """Read a collection login interactively, masking the password."""
    if password_reader is None and not sys.stdin.isatty():
        raise RuntimeError("IQ_COLLECTION_INTERACTIVE_TERMINAL_REQUIRED")
    read_password = password_reader or getpass.getpass
    username = username_reader("E-mail da conta exclusiva de coleta: ")
    # getpass otherwise falls back to echoed stdin when terminal control fails.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = read_password("Senha (oculta): ")
        except getpass.GetPassWarning:
            raise RuntimeError("IQ_COLLECTION_SECURE_INPUT_UNAVAILABLE") from None
    store_keyring_credentials(username, password)


def keyring_credentials_available() -> bool:
    """Return availability without disclosing credential identity or contents."""
    try:
        credential = _keyring_credentials()
    except Exception:
        return False
    del credential
    return True


def _vps_environment_credentials() -> Credentials:
    username = os.environ.get("IQ_EMAIL", "")
    password = os.environ.get("IQ_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_UNAVAILABLE")
    return Credentials(username=SecretStr(username), password=SecretStr(password))


def _load_keyring_module() -> Any:
    try:
        if os.name == "nt":
            # Explicit native backend: never use a configured plaintext/file fallback.
            backend = importlib.import_module("keyring.backends.Windows").WinVaultKeyring()
            if backend.priority <= 0:
                raise RuntimeError("IQ_COLLECTION_CREDENTIALS_UNAVAILABLE")
            return backend
        return importlib.import_module("keyring")
    except Exception:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_UNAVAILABLE") from None


def _keyring_credentials() -> Credentials:
    keyring = _load_keyring_module()
    credential = keyring.get_credential(KEYRING_SERVICE, None)
    if credential is None:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_UNAVAILABLE")
    username = getattr(credential, "username", "")
    password = getattr(credential, "password", "")
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not username
        or not password
    ):
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_INVALID")
    return Credentials(username=SecretStr(username), password=SecretStr(password))
