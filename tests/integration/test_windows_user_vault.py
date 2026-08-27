from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
import shutil
import sys
from pathlib import Path

import pytest

from apps.auth_agent import vault_factory
from packages.security.dpapi import VaultDecryptionError
from packages.security.secrets import SecretValue, SimulatedUserScopedVault
from packages.security.windows_vault import (
    VaultAccessControlError,
    VaultIntegrityError,
    VaultStorageError,
    WindowsUserScopedVault,
    _current_user_sid_string,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows vault uses DPAPI")


def _vault_path(directory: Path, key: str) -> Path:
    digest = hashlib.sha256(key.strip().encode("utf-8")).hexdigest()
    return directory / f"{digest}.vault"


def _dacl_sddl(path: Path) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    advapi32.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = ctypes.c_ulong
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(ctypes.c_ulong),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x00000004,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    assert result == 0
    descriptor_text = ctypes.c_wchar_p()
    try:
        assert advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            0x00000004,
            ctypes.byref(descriptor_text),
            None,
        )
        assert descriptor_text.value is not None
        return descriptor_text.value
    finally:
        if descriptor_text:
            kernel32.LocalFree(ctypes.cast(descriptor_text, ctypes.c_void_p))
        if descriptor:
            kernel32.LocalFree(descriptor)


def test_windows_vault_persists_across_reopen_and_redacts_storage(tmp_path: Path) -> None:
    directory = tmp_path / "user-vault"
    secret = secrets.token_bytes(96)
    first = WindowsUserScopedVault(directory)

    first.set_secret("lease-signing-material", SecretValue(secret))

    persisted = _vault_path(directory, "lease-signing-material").read_bytes()
    assert secret not in persisted
    assert first.has_secret("lease-signing-material")
    assert repr(first) == "WindowsUserScopedVault(<redacted>)"

    reopened = WindowsUserScopedVault(directory)
    restored = reopened.get_secret("lease-signing-material")
    assert restored is not None
    assert restored.reveal_bytes() == secret


def test_windows_vault_directory_and_file_dacl_only_allow_current_sid(tmp_path: Path) -> None:
    directory = tmp_path / "acl-vault"
    key = "acl-key"
    vault = WindowsUserScopedVault(directory)
    vault.set_secret(key, SecretValue(secrets.token_bytes(32)))
    current_sid = _current_user_sid_string()

    directory_sddl = _dacl_sddl(directory)
    file_sddl = _dacl_sddl(_vault_path(directory, key))

    assert directory_sddl.startswith("D:P")
    assert file_sddl.startswith("D:P")
    assert directory_sddl.count("(A;") == 1
    assert file_sddl.count("(A;") == 1
    assert current_sid in directory_sddl
    assert current_sid in file_sddl


def test_windows_vault_legacy_interface_remains_compatible(tmp_path: Path) -> None:
    vault = WindowsUserScopedVault(tmp_path / "legacy-vault")
    secret = SecretValue(secrets.token_bytes(32))

    vault.store("device-private-material", secret)
    loaded = vault.load("device-private-material")

    assert loaded is not None
    assert loaded.reveal_bytes() == secret.reveal_bytes()
    vault.delete("device-private-material")
    assert vault.load("device-private-material") is None


def test_windows_vault_detects_truncation_and_corruption(tmp_path: Path) -> None:
    directory = tmp_path / "integrity-vault"
    vault = WindowsUserScopedVault(directory)
    key = "refresh-material"
    vault.set_secret(key, SecretValue(secrets.token_bytes(64)))
    path = _vault_path(directory, key)
    original = path.read_bytes()

    path.write_bytes(original[:11])
    with pytest.raises(VaultIntegrityError, match="VAULT_INTEGRITY_FAILED"):
        vault.get_secret(key)

    corrupted = bytearray(original)
    corrupted[-1] ^= 0x01
    path.write_bytes(corrupted)
    with pytest.raises(VaultIntegrityError, match="VAULT_INTEGRITY_FAILED"):
        vault.get_secret(key)


def test_windows_vault_binds_ciphertext_to_logical_key(tmp_path: Path) -> None:
    directory = tmp_path / "key-binding-vault"
    vault = WindowsUserScopedVault(directory)
    source_key = "source-key"
    target_key = "target-key"
    vault.set_secret(source_key, SecretValue(secrets.token_bytes(32)))
    shutil.copyfile(_vault_path(directory, source_key), _vault_path(directory, target_key))

    with pytest.raises(VaultDecryptionError, match="VAULT_DECRYPTION_FAILED"):
        vault.get_secret(target_key)


def test_windows_vault_delete_and_clear_are_scoped(tmp_path: Path) -> None:
    directory = tmp_path / "clear-vault"
    vault = WindowsUserScopedVault(directory)
    unrelated = directory / "keep.txt"
    unrelated.write_text("non-sensitive-marker", encoding="utf-8")
    vault.set_secret("first", SecretValue(secrets.token_bytes(24)))
    vault.set_secret("second", SecretValue(secrets.token_bytes(24)))

    assert vault.delete_secret("first") is True
    assert vault.delete_secret("first") is False
    assert vault.has_secret("second")

    vault.clear()

    assert not vault.has_secret("second")
    assert unrelated.read_text(encoding="utf-8") == "non-sensitive-marker"


def test_windows_vault_atomic_replace_failure_leaves_no_partial_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "atomic-vault"
    vault = WindowsUserScopedVault(directory)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(VaultStorageError, match="VAULT_STORAGE_FAILED"):
        vault.set_secret("atomic-key", SecretValue(secrets.token_bytes(32)))

    assert not _vault_path(directory, "atomic-key").exists()
    assert list(directory.glob("*.tmp")) == []


def test_windows_vault_supports_long_profile_paths(tmp_path: Path) -> None:
    directory = tmp_path / "vault"
    while len(str(directory)) < 155:
        directory = directory / "long-profile"

    vault = WindowsUserScopedVault(directory)
    original = SecretValue(secrets.token_bytes(32))

    vault.set_secret("auth-agent-key-registry", original)

    restored = vault.get_secret("auth-agent-key-registry")
    assert restored is not None
    assert restored.reveal_bytes() == original.reveal_bytes()


def test_factory_uses_explicit_simulation_and_never_masks_windows_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    simulated = vault_factory.create_user_scoped_vault(
        tmp_path / "unused",
        force_simulation=True,
    )
    assert isinstance(simulated, SimulatedUserScopedVault)

    selected = vault_factory.create_user_scoped_vault(tmp_path / "selected-windows-vault")
    assert isinstance(selected, WindowsUserScopedVault)

    def fail_windows_vault(_profile_dir: Path) -> WindowsUserScopedVault:
        raise VaultAccessControlError()

    monkeypatch.setattr(vault_factory, "WindowsUserScopedVault", fail_windows_vault)
    with pytest.raises(VaultAccessControlError, match="VAULT_ACL_FAILED"):
        vault_factory.create_user_scoped_vault(tmp_path / "must-fail")
