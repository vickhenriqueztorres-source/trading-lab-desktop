"""R-MAN-4: Ed25519. No key loading, credential storage, network or logging here."""

import base64
import binascii
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ValidationError

from manifest_schema.canonical import canonical_bytes
from manifest_schema.models import Manifest

# Public test vector key; ONLY the fingerprint/public half is shipped by the package.
# The publicly disclosed test seed is restricted to tests/keys/. Never a production trust root.
TEST_PUBLIC_KEY = bytes.fromhex("03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8")


def _validated(manifest: Manifest | dict[str, Any]) -> Manifest:
    data = (
        manifest.model_dump(mode="python", exclude_unset=True)
        if isinstance(manifest, Manifest)
        else manifest
    )
    return Manifest.model_validate(data)


def unsigned_document(manifest: Manifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude={"signature"}, exclude_unset=True)


def sign(
    manifest: Manifest | dict[str, Any],
    private_key: bytes,
    key_id: str,
    *,
    allow_test_keys: bool = False,
) -> Manifest:
    """Sign validated data; key_id is included in signed bytes. Test use is explicit."""
    data = (
        manifest.model_dump(mode="python", exclude_unset=True)
        if isinstance(manifest, Manifest)
        else dict(manifest)
    )
    data["key_id"] = key_id
    data["signature"] = ""
    candidate = Manifest.model_validate(data)
    key = Ed25519PrivateKey.from_private_bytes(private_key)
    if key.public_key().public_bytes_raw() == TEST_PUBLIC_KEY and not allow_test_keys:
        raise ValueError("MANIFEST_TEST_KEY_FORBIDDEN")
    signature = key.sign(canonical_bytes(unsigned_document(candidate)))
    data["signature"] = "ed25519:" + base64.b64encode(signature).decode("ascii")
    return Manifest.model_validate(data)


def verify(
    manifest: Manifest | dict[str, Any],
    public_keys: dict[str, bytes],
    *,
    allow_test_keys: bool = False,
) -> bool:
    """Fail closed on mutated models, unknown key IDs, malformed keys and signatures."""
    try:
        candidate = _validated(manifest)
        raw_key = public_keys[candidate.key_id]
        if raw_key == TEST_PUBLIC_KEY and not allow_test_keys:
            return False
        if not candidate.signature.startswith("ed25519:"):
            return False
        encoded = candidate.signature.removeprefix("ed25519:")
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != encoded:
            return False
        key = Ed25519PublicKey.from_public_bytes(raw_key)
        key.verify(signature, canonical_bytes(unsigned_document(candidate)))
        return True
    except (ValidationError, InvalidSignature, ValueError, TypeError, KeyError, binascii.Error):
        return False
