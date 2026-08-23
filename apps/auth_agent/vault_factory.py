from __future__ import annotations

import os
import sys
from pathlib import Path

from packages.security.secrets import SimulatedUserScopedVault
from packages.security.vault import UserScopedVaultProtocol
from packages.security.windows_vault import WindowsUserScopedVault


def create_user_scoped_vault(
    profile_dir: Path | None = None,
    force_simulation: bool = False,
) -> UserScopedVaultProtocol:
    """Select the platform vault without masking Windows protection failures."""

    if force_simulation or sys.platform != "win32":
        return SimulatedUserScopedVault("local-simulation")
    if profile_dir is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("VAULT_PROFILE_UNAVAILABLE")
        profile_dir = Path(local_app_data) / "DualTrade" / "vault"
    return WindowsUserScopedVault(profile_dir)
