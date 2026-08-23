from __future__ import annotations

import threading
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True, repr=False)
class SecretValue:
    """A deliberately redacted container for short-lived sensitive values."""

    _value: bytes

    @classmethod
    def from_text(cls, value: str) -> SecretValue:
        if not value:
            raise ValueError("secret cannot be empty")
        return cls(value.encode("utf-8"))

    def reveal_bytes(self) -> bytes:
        return self._value

    def reveal_text(self) -> str:
        return self._value.decode("utf-8")

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    __str__ = __repr__


class UserScopedVault(Protocol):
    def store(self, name: str, value: SecretValue) -> None: ...

    def load(self, name: str) -> SecretValue | None: ...

    def delete(self, name: str) -> None: ...


class SimulatedUserScopedVault:
    """In-memory Phase 0 stand-in for Windows current-user secret protection.

    The backing mapping can be reused to simulate an Auth Agent restart. Scope is
    part of every key so another simulated Windows user cannot read the values.
    """

    def __init__(
        self,
        user_scope: str,
        backing: MutableMapping[str, bytes] | None = None,
    ) -> None:
        normalized_scope = user_scope.strip()
        if not normalized_scope:
            raise ValueError("user_scope cannot be empty")
        self._scope = normalized_scope
        self._backing = backing if backing is not None else {}
        self._lock = threading.Lock()

    def _key(self, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("vault key cannot be empty")
        return f"{self._scope}:{normalized}"

    def store(self, name: str, value: SecretValue) -> None:
        self.set_secret(name, value)

    def set_secret(self, key: str, value: SecretValue) -> None:
        with self._lock:
            self._backing[self._key(key)] = bytes(value.reveal_bytes())

    def load(self, name: str) -> SecretValue | None:
        return self.get_secret(name)

    def get_secret(self, key: str) -> SecretValue | None:
        with self._lock:
            value = self._backing.get(self._key(key))
        return None if value is None else SecretValue(bytes(value))

    def delete(self, name: str) -> None:
        self.delete_secret(name)

    def delete_secret(self, key: str) -> bool:
        with self._lock:
            return self._backing.pop(self._key(key), None) is not None

    def has_secret(self, key: str) -> bool:
        with self._lock:
            return self._key(key) in self._backing

    def clear(self) -> None:
        prefix = f"{self._scope}:"
        with self._lock:
            scoped_keys = [key for key in self._backing if key.startswith(prefix)]
            for key in scoped_keys:
                del self._backing[key]

    def __repr__(self) -> str:
        return "SimulatedUserScopedVault(<redacted>)"
