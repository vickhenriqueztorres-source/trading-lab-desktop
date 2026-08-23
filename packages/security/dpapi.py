from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x00000001
PROTECTION_FLAGS = CRYPTPROTECT_UI_FORBIDDEN
_MAX_DATA_BYTES = 1024 * 1024
_MAX_ENTROPY_BYTES = 4096
_MAX_DESCRIPTION_CHARS = 128


class VaultDPAPIError(RuntimeError):
    reason_code = "VAULT_DPAPI_FAILED"

    def __init__(self, *, winerror_code: int | None = None) -> None:
        self.winerror_code = winerror_code
        super().__init__(self.reason_code)


class VaultPlatformError(VaultDPAPIError):
    reason_code = "VAULT_PLATFORM_UNSUPPORTED"


class VaultEncryptionError(VaultDPAPIError):
    reason_code = "VAULT_ENCRYPTION_FAILED"


class VaultDecryptionError(VaultDPAPIError):
    reason_code = "VAULT_DECRYPTION_FAILED"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _validate_input(data: bytes, *, limit: int, field: str) -> None:
    if not isinstance(data, bytes) or not data:
        raise ValueError(f"{field} must be non-empty bytes")
    if len(data) > limit:
        raise ValueError(f"{field} exceeds size limit")


def _buffer_blob(data: bytes) -> tuple[ctypes.Array[ctypes.c_char], DATA_BLOB]:
    buffer = ctypes.create_string_buffer(data, len(data))
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return buffer, DATA_BLOB(len(data), pointer)


def _zero_buffer(buffer: ctypes.Array[ctypes.c_char] | None) -> None:
    if buffer is not None:
        ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))


def _load_libraries() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    if sys.platform != "win32":
        raise VaultPlatformError()
    return (
        ctypes.WinDLL("crypt32", use_last_error=True),
        ctypes.WinDLL("kernel32", use_last_error=True),
    )


def _configure_functions(crypt32: ctypes.WinDLL, kernel32: ctypes.WinDLL) -> None:
    blob_pointer = ctypes.POINTER(DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        wintypes.LPCWSTR,
        blob_pointer,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        wintypes.LPVOID,
        blob_pointer,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL


def _copy_and_release_output(output: DATA_BLOB, kernel32: ctypes.WinDLL) -> bytes:
    if not output.pbData or output.cbData == 0:
        raise VaultDPAPIError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(output.pbData, 0, output.cbData)
        kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))


def win32_crypt_protect_data(
    data: bytes,
    entropy: bytes | None = None,
    description: str = "",
) -> bytes:
    """Protect bytes for the current Windows logon user without showing UI."""

    _validate_input(data, limit=_MAX_DATA_BYTES, field="data")
    if entropy is not None:
        _validate_input(entropy, limit=_MAX_ENTROPY_BYTES, field="entropy")
    if len(description) > _MAX_DESCRIPTION_CHARS:
        raise ValueError("description exceeds size limit")

    crypt32, kernel32 = _load_libraries()
    _configure_functions(crypt32, kernel32)
    data_buffer, data_blob = _buffer_blob(data)
    entropy_buffer: ctypes.Array[ctypes.c_char] | None = None
    entropy_blob: DATA_BLOB | None = None
    if entropy is not None:
        entropy_buffer, entropy_blob = _buffer_blob(entropy)
    output = DATA_BLOB()
    try:
        success = crypt32.CryptProtectData(
            ctypes.byref(data_blob),
            description or None,
            None if entropy_blob is None else ctypes.byref(entropy_blob),
            None,
            None,
            PROTECTION_FLAGS,
            ctypes.byref(output),
        )
        if not success:
            raise VaultEncryptionError(winerror_code=ctypes.get_last_error())
        try:
            return _copy_and_release_output(output, kernel32)
        except VaultDPAPIError as exc:
            raise VaultEncryptionError(winerror_code=exc.winerror_code) from exc
    finally:
        _zero_buffer(data_buffer)
        _zero_buffer(entropy_buffer)


def win32_crypt_unprotect_data(data: bytes, entropy: bytes | None = None) -> bytes:
    """Unprotect bytes in the same Windows user context without showing UI."""

    _validate_input(data, limit=_MAX_DATA_BYTES, field="data")
    if entropy is not None:
        _validate_input(entropy, limit=_MAX_ENTROPY_BYTES, field="entropy")

    crypt32, kernel32 = _load_libraries()
    _configure_functions(crypt32, kernel32)
    data_buffer, data_blob = _buffer_blob(data)
    entropy_buffer: ctypes.Array[ctypes.c_char] | None = None
    entropy_blob: DATA_BLOB | None = None
    if entropy is not None:
        entropy_buffer, entropy_blob = _buffer_blob(entropy)
    output = DATA_BLOB()
    try:
        success = crypt32.CryptUnprotectData(
            ctypes.byref(data_blob),
            None,
            None if entropy_blob is None else ctypes.byref(entropy_blob),
            None,
            None,
            PROTECTION_FLAGS,
            ctypes.byref(output),
        )
        if not success:
            raise VaultDecryptionError(winerror_code=ctypes.get_last_error())
        try:
            return _copy_and_release_output(output, kernel32)
        except VaultDPAPIError as exc:
            raise VaultDecryptionError(winerror_code=exc.winerror_code) from exc
    finally:
        _zero_buffer(data_buffer)
        _zero_buffer(entropy_buffer)
