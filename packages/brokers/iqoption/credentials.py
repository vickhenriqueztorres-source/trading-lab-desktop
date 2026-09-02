"""Protected IQ Option credentials for explicitly selected account access.

The credential object is a storage boundary for the isolated IQ Option
read-only connector; it does not authenticate or submit orders by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packages.security import SecretValue, WindowsUserScopedVault

IQOPTION_EMAIL_KEY = "iqoption.practice.email"
IQOPTION_PASSWORD_KEY = "iqoption.practice.password"
IQOPTION_ACCOUNT_MODE_KEY = "iqoption.practice.account_mode"


@dataclass(frozen=True, slots=True, repr=False)
class IQOptionCredentials:
    """Credentials accepted by the isolated IQ Option worker boundary."""

    email: str
    password: SecretValue
    account_mode: str = "practice"

    def __post_init__(self) -> None:
        normalized_email = self.email.strip().lower()
        if "@" not in normalized_email or len(normalized_email) > 254:
            raise ValueError("IQ Option email is invalid")
        if not self.password.reveal_bytes():
            raise ValueError("IQ Option password is required")
        normalized_mode = self.account_mode.strip().lower()
        if normalized_mode not in {"practice", "real"}:
            raise ValueError("IQ Option account mode is invalid")
        object.__setattr__(self, "email", normalized_email)
        object.__setattr__(self, "account_mode", normalized_mode)

    def __repr__(self) -> str:
        return "IQOptionCredentials(<redacted>)"


class IQOptionCredentialVault:
    """DPAPI CurrentUser storage for IQ Option credentials."""

    def __init__(self, directory: Path) -> None:
        self._vault = WindowsUserScopedVault(Path(directory))

    def save(self, credentials: IQOptionCredentials) -> None:
        self._vault.set_secret(IQOPTION_EMAIL_KEY, SecretValue.from_text(credentials.email))
        self._vault.set_secret(IQOPTION_PASSWORD_KEY, credentials.password)
        self._vault.set_secret(
            IQOPTION_ACCOUNT_MODE_KEY,
            SecretValue.from_text(credentials.account_mode),
        )

    def load(self) -> IQOptionCredentials | None:
        email = self._vault.get_secret(IQOPTION_EMAIL_KEY)
        password = self._vault.get_secret(IQOPTION_PASSWORD_KEY)
        mode = self._vault.get_secret(IQOPTION_ACCOUNT_MODE_KEY)
        if email is None and password is None and mode is None:
            return None
        if email is None or password is None or mode is None:
            raise ValueError("IQOPTION_CREDENTIAL_VAULT_INCOMPLETE")
        return IQOptionCredentials(
            email=email.reveal_text(),
            password=password,
            account_mode=mode.reveal_text(),
        )

    def configured_account_mode(self) -> str | None:
        """Return only the non-secret saved mode without materializing credentials."""

        mode = self._vault.get_secret(IQOPTION_ACCOUNT_MODE_KEY)
        if mode is None:
            return None
        normalized = mode.reveal_text().strip().lower()
        if normalized not in {"practice", "real"}:
            raise ValueError("IQOPTION_CREDENTIAL_VAULT_INVALID_MODE")
        return normalized

    def clear(self) -> None:
        self._vault.delete_secret(IQOPTION_PASSWORD_KEY)
        self._vault.delete_secret(IQOPTION_ACCOUNT_MODE_KEY)
        self._vault.delete_secret(IQOPTION_EMAIL_KEY)

    def is_configured(self) -> bool:
        return self.load() is not None


__all__ = ["IQOptionCredentials", "IQOptionCredentialVault"]
