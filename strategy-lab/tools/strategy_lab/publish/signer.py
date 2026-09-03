"""Ed25519 private key loader with 0600 permission check and signing (R-PUB-4)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from manifest_schema.models import Manifest
from manifest_schema.signing import sign

DEFAULT_KEYS_DIR = Path.home() / ".strategy-lab" / "keys"


class InsecureKeyFileError(PermissionError):
    """Raised when the private key file does not have strict 0600 permissions."""


def verify_key_permissions(path: Path) -> None:
    """R-PUB-4: verify file mode is 0600 (refuse any other permissions e.g. 0644)."""
    if os.name == "nt":
        return
    file_stat = path.stat()
    mode = stat.S_IMODE(file_stat.st_mode)

    # In POSIX mode == 0o600 (read/write for owner only, 0 for group and others).
    # If any group or other bits are set (0o077), reject as insecure.
    if (mode & 0o077) != 0:
        raise InsecureKeyFileError(
            f"Permissões inseguras no arquivo de chave '{path}': {oct(mode)}. "
            f"Exige estritamente 0600 (chmod 600)."
        )


def load_private_key_bytes(
    key_id: str,
    keys_dir: Path | None = None,
    verify_perms: bool = True,
) -> bytes:
    """Load raw 32-byte Ed25519 private key from ~/.strategy-lab/keys/{key_id}.pem."""
    if key_id not in ("A", "B", "TEST"):
        raise ValueError(f"key_id inválido: {key_id}. Deve ser 'A' ou 'B'.")

    base_dir = keys_dir or Path(os.environ.get("STRATEGY_LAB_KEYS_DIR", str(DEFAULT_KEYS_DIR)))
    key_path = base_dir / f"{key_id}.pem"

    if not key_path.exists():
        raise FileNotFoundError(f"Chave privada não encontrada em: {key_path}")

    if verify_perms:
        verify_key_permissions(key_path)

    key_bytes = key_path.read_bytes()

    # Try PEM format first
    try:
        loaded = load_pem_private_key(key_bytes, password=None)
        if isinstance(loaded, Ed25519PrivateKey):
            return loaded.private_bytes_raw()
    except Exception:
        pass

    # Try raw 32-byte private key or hex
    if len(key_bytes) == 32:
        return key_bytes

    try:
        hex_text = key_bytes.decode("ascii").strip()
        if len(hex_text) == 64:
            return bytes.fromhex(hex_text)
    except Exception:
        pass

    raise ValueError(f"Não foi possível interpretar a chave privada Ed25519 em {key_path}")


def load_private_key(
    key_id: str,
    keys_dir: Path | None = None,
    verify_perms: bool = True,
) -> Ed25519PrivateKey:
    """Load Ed25519PrivateKey instance for key_id ('A' or 'B')."""
    raw_key = load_private_key_bytes(key_id, keys_dir=keys_dir, verify_perms=verify_perms)
    return Ed25519PrivateKey.from_private_bytes(raw_key)


def sign_manifest(
    manifest: Manifest | dict[str, Any],
    private_key_bytes: bytes,
    key_id: str,
    allow_test_keys: bool = False,
) -> Manifest:
    """Sign an unsigned manifest using the Ed25519 private key bytes."""
    return sign(manifest, private_key_bytes, key_id=key_id, allow_test_keys=allow_test_keys)
