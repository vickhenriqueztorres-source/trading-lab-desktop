"""DPAPI-protected persistence for reusable IQ Option WebSocket sessions."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from packages.security import (
    SecretValue,
    UserScopedVaultProtocol,
    VaultDPAPIError,
    VaultError,
    WindowsUserScopedVault,
)

_DEFAULT_TTL_SECONDS = 20 * 60 * 60
_KEY_PREFIX = "iqoption.session."


@dataclass(frozen=True, slots=True, repr=False)
class StoredSession:
    ssid: SecretValue
    account_mode: str
    saved_at: float
    expires_at: float

    def __repr__(self) -> str:
        return (
            "StoredSession(ssid=<redacted>, "
            f"account_mode={self.account_mode!r}, saved_at={self.saved_at!r}, "
            f"expires_at={self.expires_at!r})"
        )


class SsidStore:
    """Store one encrypted session per account mode under the Windows user identity."""

    def __init__(
        self,
        directory: Path,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        wall_time: Callable[[], float] = time.time,
        vault: UserScopedVaultProtocol | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("IQ Option session TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._wall_time = wall_time
        self._vault = vault or WindowsUserScopedVault(Path(directory))

    def save(self, ssid: SecretValue, account_mode: str) -> None:
        mode = self._mode(account_mode)
        now = float(self._wall_time())
        payload = json.dumps(
            {
                "ssid": ssid.reveal_text(),
                "account_mode": mode,
                "saved_at": now,
                "expires_at": now + self._ttl_seconds,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self._vault.set_secret(self._key(mode), SecretValue.from_text(payload))

    def load(self, account_mode: str) -> StoredSession | None:
        mode = self._mode(account_mode)
        try:
            protected = self._vault.get_secret(self._key(mode))
            if protected is None:
                return None
            raw = json.loads(protected.reveal_text())
            if not isinstance(raw, dict):
                raise ValueError("invalid IQ Option session payload")
            stored_mode = self._mode(str(raw["account_mode"]))
            ssid = raw["ssid"]
            saved_at = float(raw["saved_at"])
            expires_at = float(raw["expires_at"])
            if (
                stored_mode != mode
                or not isinstance(ssid, str)
                or not ssid
                or saved_at < 0
                or expires_at <= saved_at
                or float(self._wall_time()) >= expires_at
            ):
                self.clear(mode)
                return None
            return StoredSession(SecretValue.from_text(ssid), mode, saved_at, expires_at)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, VaultDPAPIError, VaultError):
            self._clear_best_effort(mode)
            return None

    def clear(self, account_mode: str) -> None:
        self._vault.delete_secret(self._key(self._mode(account_mode)))

    def _clear_best_effort(self, account_mode: str) -> None:
        with suppress(OSError, RuntimeError, ValueError):
            self.clear(account_mode)

    @staticmethod
    def _mode(account_mode: str) -> str:
        mode = account_mode.strip().lower()
        if mode not in {"practice", "real"}:
            raise ValueError("IQ Option account mode is invalid")
        return mode

    @staticmethod
    def _key(account_mode: str) -> str:
        return f"{_KEY_PREFIX}{account_mode}"


__all__ = ["SsidStore", "StoredSession"]
