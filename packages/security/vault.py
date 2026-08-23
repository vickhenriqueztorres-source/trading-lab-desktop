from __future__ import annotations

from typing import Protocol

from packages.security.secrets import SecretValue, UserScopedVault


class UserScopedVaultProtocol(UserScopedVault, Protocol):
    """Secret storage contract used at the Auth Agent boundary."""

    def set_secret(self, key: str, value: SecretValue) -> None: ...

    def get_secret(self, key: str) -> SecretValue | None: ...

    def delete_secret(self, key: str) -> bool: ...

    def has_secret(self, key: str) -> bool: ...

    def clear(self) -> None: ...
