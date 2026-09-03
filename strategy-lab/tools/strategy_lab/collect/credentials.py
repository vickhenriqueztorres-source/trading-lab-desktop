"""Lab-only credentials: OS keyring / explicit VPS environment (R-COL-1, I-8)."""

from __future__ import annotations

import importlib
import os
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


def _vps_environment_credentials() -> Credentials:
    username = os.environ.get("IQ_EMAIL", "")
    password = os.environ.get("IQ_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("IQ_COLLECTION_CREDENTIALS_UNAVAILABLE")
    return Credentials(username=SecretStr(username), password=SecretStr(password))


def _load_keyring_module() -> Any:
    try:
        return importlib.import_module("keyring")
    except ImportError:
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
