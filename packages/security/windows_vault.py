from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import struct
import sys
import threading
import uuid
from contextlib import suppress
from pathlib import Path

from packages.security.dpapi import (
    VaultPlatformError,
    win32_crypt_protect_data,
    win32_crypt_unprotect_data,
)
from packages.security.secrets import SecretValue

_VAULT_SUFFIX = ".vault"
_OUTER_MAGIC = b"DTVAULT1"
_INNER_MAGIC = b"DTSEC1"
_VERSION = 1
_OUTER_HEADER = struct.Struct(">8sBI")
_INNER_HEADER = struct.Struct(">6s32sI")
_CHECKSUM_BYTES = hashlib.sha256().digest_size
_MAX_KEY_CHARS = 128
_MAX_SECRET_BYTES = 512 * 1024
_MAX_CIPHERTEXT_BYTES = 1024 * 1024
_MAX_VAULT_FILES = 256
_ENTROPY_DOMAIN = b"DualTrade.WindowsUserScopedVault.v1\x00"


class VaultError(RuntimeError):
    reason_code = "VAULT_FAILED"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


class VaultConfigurationError(VaultError):
    reason_code = "VAULT_CONFIGURATION_INVALID"


class VaultStorageError(VaultError):
    reason_code = "VAULT_STORAGE_FAILED"


class VaultIntegrityError(VaultError):
    reason_code = "VAULT_INTEGRITY_FAILED"


class VaultAccessControlError(VaultError):
    reason_code = "VAULT_ACL_FAILED"


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_ulong)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


def _current_user_sid_string() -> str:
    if sys.platform != "win32":
        raise VaultPlatformError()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    token = ctypes.c_void_p()
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    advapi32.GetTokenInformation.restype = ctypes.c_int
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise VaultAccessControlError()
    try:
        required = ctypes.c_ulong()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if required.value == 0:
            raise VaultAccessControlError()
        token_info = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            1,
            token_info,
            required,
            ctypes.byref(required),
        ):
            raise VaultAccessControlError()
        token_user = ctypes.cast(token_info, ctypes.POINTER(_TOKEN_USER)).contents
        sid_text_pointer = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(
            token_user.User.Sid,
            ctypes.byref(sid_text_pointer),
        ):
            raise VaultAccessControlError()
        try:
            if sid_text_pointer.value is None:
                raise VaultAccessControlError()
            return sid_text_pointer.value
        finally:
            kernel32.LocalFree(ctypes.cast(sid_text_pointer, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)


def _restrict_to_current_user(path: Path, *, directory: bool) -> None:
    """Replace the DACL with a protected allow entry for the current token SID."""

    sid = _current_user_sid_string()
    inheritance = "OICI" if directory else ""
    descriptor_text = f"D:P(A;{inheritance};FA;;;{sid})"
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_ulong()
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_ulong),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = ctypes.c_int
    advapi32.SetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = ctypes.c_ulong
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        descriptor_text,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise VaultAccessControlError()
    try:
        dacl_present = ctypes.c_int()
        dacl_defaulted = ctypes.c_int()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            raise VaultAccessControlError()
        if not dacl_present.value or not dacl.value:
            raise VaultAccessControlError()
        result = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            0x00000004 | 0x80000000,
            None,
            None,
            dacl,
            None,
        )
        if result != 0:
            raise VaultAccessControlError()
    finally:
        kernel32.LocalFree(descriptor)


def _normalize_key(key: str) -> str:
    if not isinstance(key, str):
        raise TypeError("vault key must be a string")
    normalized = key.strip()
    if not normalized or len(normalized) > _MAX_KEY_CHARS or "\x00" in normalized:
        raise ValueError("vault key is invalid")
    return normalized


def _key_digest(key: str) -> bytes:
    return hashlib.sha256(_normalize_key(key).encode("utf-8")).digest()


def _entropy(digest: bytes) -> bytes:
    return hashlib.sha256(_ENTROPY_DOMAIN + digest).digest()


def _encode_plaintext(digest: bytes, secret: bytes) -> bytes:
    if not secret or len(secret) > _MAX_SECRET_BYTES:
        raise ValueError("secret size is invalid")
    header = _INNER_HEADER.pack(_INNER_MAGIC, digest, len(secret))
    body = header + secret
    return body + hashlib.sha256(body).digest()


def _decode_plaintext(digest: bytes, payload: bytes) -> bytes:
    minimum = _INNER_HEADER.size + _CHECKSUM_BYTES + 1
    if len(payload) < minimum:
        raise VaultIntegrityError()
    header = payload[: _INNER_HEADER.size]
    magic, stored_digest, secret_length = _INNER_HEADER.unpack(header)
    expected_length = _INNER_HEADER.size + secret_length + _CHECKSUM_BYTES
    if (
        magic != _INNER_MAGIC
        or not hmac.compare_digest(stored_digest, digest)
        or secret_length == 0
        or secret_length > _MAX_SECRET_BYTES
        or len(payload) != expected_length
    ):
        raise VaultIntegrityError()
    body = payload[:-_CHECKSUM_BYTES]
    checksum = payload[-_CHECKSUM_BYTES:]
    if not hmac.compare_digest(hashlib.sha256(body).digest(), checksum):
        raise VaultIntegrityError()
    return bytes(payload[_INNER_HEADER.size : -_CHECKSUM_BYTES])


def _encode_envelope(ciphertext: bytes) -> bytes:
    if not ciphertext or len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
        raise VaultStorageError()
    header = _OUTER_HEADER.pack(_OUTER_MAGIC, _VERSION, len(ciphertext))
    body = header + ciphertext
    return body + hashlib.sha256(body).digest()


def _decode_envelope(envelope: bytes) -> bytes:
    minimum = _OUTER_HEADER.size + _CHECKSUM_BYTES + 1
    if len(envelope) < minimum:
        raise VaultIntegrityError()
    header = envelope[: _OUTER_HEADER.size]
    magic, version, ciphertext_length = _OUTER_HEADER.unpack(header)
    expected_length = _OUTER_HEADER.size + ciphertext_length + _CHECKSUM_BYTES
    if (
        magic != _OUTER_MAGIC
        or version != _VERSION
        or ciphertext_length == 0
        or ciphertext_length > _MAX_CIPHERTEXT_BYTES
        or len(envelope) != expected_length
    ):
        raise VaultIntegrityError()
    body = envelope[:-_CHECKSUM_BYTES]
    checksum = envelope[-_CHECKSUM_BYTES:]
    if not hmac.compare_digest(hashlib.sha256(body).digest(), checksum):
        raise VaultIntegrityError()
    return bytes(envelope[_OUTER_HEADER.size : -_CHECKSUM_BYTES])


class WindowsUserScopedVault:
    """Persistent DPAPI CurrentUser vault with per-file integrity envelopes."""

    def __init__(self, profile_dir: Path) -> None:
        if sys.platform != "win32":
            raise VaultPlatformError()
        candidate = Path(profile_dir)
        if candidate.exists() and (candidate.is_symlink() or not candidate.is_dir()):
            raise VaultConfigurationError()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise VaultStorageError() from exc
        if resolved.parent == resolved:
            raise VaultConfigurationError()
        _restrict_to_current_user(resolved, directory=True)
        self._directory = resolved
        self._lock = threading.RLock()

    def _path(self, key: str) -> tuple[bytes, Path]:
        digest = _key_digest(key)
        return digest, self._directory / f"{digest.hex()}{_VAULT_SUFFIX}"

    def _atomic_write(self, destination: Path, data: bytes) -> None:
        # Keep the staging name independent of the destination digest. Repeating
        # the 64-character digest in the temporary filename pushed otherwise
        # valid profile paths past the legacy Windows MAX_PATH boundary and made
        # the Auth Agent fail before the UI could open.
        temporary = self._directory / f".{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _restrict_to_current_user(temporary, directory=False)
            os.replace(temporary, destination)
            _restrict_to_current_user(destination, directory=False)
        except OSError as exc:
            raise VaultStorageError() from exc
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def set_secret(self, key: str, value: SecretValue) -> None:
        digest, destination = self._path(key)
        if destination.exists() and (destination.is_symlink() or not destination.is_file()):
            raise VaultStorageError()
        secret = value.reveal_bytes()
        if not isinstance(secret, bytes):
            raise TypeError("secret value must contain bytes")
        plaintext = _encode_plaintext(digest, secret)
        ciphertext = win32_crypt_protect_data(
            plaintext,
            entropy=_entropy(digest),
            description="DualTrade user-scoped vault",
        )
        envelope = _encode_envelope(ciphertext)
        with self._lock:
            self._atomic_write(destination, envelope)

    def get_secret(self, key: str) -> SecretValue | None:
        digest, source = self._path(key)
        with self._lock:
            if not source.exists():
                return None
            if source.is_symlink() or not source.is_file():
                raise VaultStorageError()
            try:
                size = source.stat().st_size
                if size <= 0 or size > _MAX_CIPHERTEXT_BYTES + 1024:
                    raise VaultIntegrityError()
                envelope = source.read_bytes()
            except OSError as exc:
                raise VaultStorageError() from exc
        ciphertext = _decode_envelope(envelope)
        plaintext = win32_crypt_unprotect_data(ciphertext, entropy=_entropy(digest))
        return SecretValue(_decode_plaintext(digest, plaintext))

    def delete_secret(self, key: str) -> bool:
        _, path = self._path(key)
        with self._lock:
            if not path.exists():
                return False
            if path.is_symlink() or not path.is_file():
                raise VaultStorageError()
            try:
                path.unlink()
            except OSError as exc:
                raise VaultStorageError() from exc
            return True

    def has_secret(self, key: str) -> bool:
        _, path = self._path(key)
        with self._lock:
            if path.is_symlink():
                raise VaultStorageError()
            return path.is_file()

    def clear(self) -> None:
        with self._lock:
            paths = list(self._directory.glob(f"*{_VAULT_SUFFIX}"))
            if len(paths) > _MAX_VAULT_FILES:
                raise VaultStorageError()
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    raise VaultStorageError()
            try:
                for path in paths:
                    path.unlink()
            except OSError as exc:
                raise VaultStorageError() from exc

    def store(self, name: str, value: SecretValue) -> None:
        self.set_secret(name, value)

    def load(self, name: str) -> SecretValue | None:
        return self.get_secret(name)

    def delete(self, name: str) -> None:
        self.delete_secret(name)

    def __repr__(self) -> str:
        return "WindowsUserScopedVault(<redacted>)"
