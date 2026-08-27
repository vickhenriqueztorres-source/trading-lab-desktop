from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packages.security import SecretValue, WindowsUserScopedVault

DERIV_ACCOUNT_ID_KEY = "deriv.options.demo_account_id"
DERIV_ACCOUNT_TYPE_KEY = "deriv.options.account_type"
DERIV_ACCESS_TOKEN_KEY = "deriv.options.access_token"


@dataclass(frozen=True, slots=True)
class DerivCredentials:
    account_id: str
    account_type: str
    access_token: SecretValue

    def __post_init__(self) -> None:
        normalized = self.account_type.strip().lower()
        if not self.account_id.strip() or normalized not in {"demo", "real"}:
            raise ValueError("Deriv account ID and type are required")
        object.__setattr__(self, "account_type", normalized)


# Backward-compatible import name for callers compiled against the Demo-only slice.
DerivDemoCredentials = DerivCredentials


class DerivCredentialVault:
    """Deriv-only DPAPI facade; no caller receives the token as plain text."""

    def __init__(self, directory: Path) -> None:
        self._vault = WindowsUserScopedVault(Path(directory))

    def save(self, credentials: DerivCredentials) -> None:
        self._vault.set_secret(
            DERIV_ACCOUNT_ID_KEY,
            SecretValue.from_text(credentials.account_id.strip()),
        )
        self._vault.set_secret(
            DERIV_ACCOUNT_TYPE_KEY,
            SecretValue.from_text(credentials.account_type),
        )
        self._vault.set_secret(DERIV_ACCESS_TOKEN_KEY, credentials.access_token)

    def load(self) -> DerivCredentials | None:
        account_id = self._vault.get_secret(DERIV_ACCOUNT_ID_KEY)
        account_type = self._vault.get_secret(DERIV_ACCOUNT_TYPE_KEY)
        token = self._vault.get_secret(DERIV_ACCESS_TOKEN_KEY)
        if account_id is None and account_type is None and token is None:
            return None
        if account_id is None or account_type is None or token is None:
            raise ValueError("DERIV_CREDENTIAL_VAULT_INCOMPLETE")
        return DerivCredentials(
            account_id=account_id.reveal_text(),
            account_type=account_type.reveal_text(),
            access_token=token,
        )

    def selected_account_type(self) -> str | None:
        value = self._vault.get_secret(DERIV_ACCOUNT_TYPE_KEY)
        if value is None:
            return None
        normalized = value.reveal_text().strip().lower()
        if normalized not in {"demo", "real"}:
            raise ValueError("DERIV_ACCOUNT_TYPE_INVALID")
        return normalized

    def clear(self) -> None:
        self._vault.delete_secret(DERIV_ACCESS_TOKEN_KEY)
        self._vault.delete_secret(DERIV_ACCOUNT_TYPE_KEY)
        self._vault.delete_secret(DERIV_ACCOUNT_ID_KEY)

    def is_configured(self) -> bool:
        return self.load() is not None
