from __future__ import annotations

from pathlib import Path

import pytest

from apps.auth_agent import vault_factory
from packages.security.secrets import SecretValue, SimulatedUserScopedVault


def test_forced_simulated_vault_implements_new_contract_and_scoped_clear(tmp_path: Path) -> None:
    del tmp_path
    backing: dict[str, bytes] = {}
    first = SimulatedUserScopedVault("first-user", backing)
    second = SimulatedUserScopedVault("second-user", backing)
    first.set_secret("lease", SecretValue(b"first-runtime-value"))
    second.set_secret("lease", SecretValue(b"second-runtime-value"))

    assert first.has_secret("lease")
    assert first.delete_secret("missing") is False
    first.clear()

    assert first.get_secret("lease") is None
    assert second.get_secret("lease") is not None
    assert repr(first) == "SimulatedUserScopedVault(<redacted>)"


def test_factory_falls_back_on_non_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vault_factory.sys, "platform", "linux")

    selected = vault_factory.create_user_scoped_vault()

    assert isinstance(selected, SimulatedUserScopedVault)
