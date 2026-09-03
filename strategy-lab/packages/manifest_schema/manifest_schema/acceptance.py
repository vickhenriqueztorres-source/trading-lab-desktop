"""R-MAN-1..7: bounded raw-document acceptance with stable, non-sensitive reason codes."""

from typing import Any

from pydantic import ValidationError

from manifest_schema.canonical import load_document
from manifest_schema.models import Manifest
from manifest_schema.signing import verify


def evaluate(
    raw: bytes,
    public_keys: dict[str, bytes],
    *,
    allow_test_keys: bool = False,
    expected_primitives_version: str | None = None,
    expected_parity_sha256: str | None = None,
) -> tuple[Manifest | None, str]:
    try:
        data: dict[str, Any] = load_document(raw)
        manifest = Manifest.model_validate(data)
    except (ValueError, TypeError, RecursionError) as error:
        if isinstance(error, ValidationError):
            for item in error.errors(include_input=False, include_context=False):
                message = item["msg"].removeprefix("Value error, ")
                if message.startswith("MANIFEST_"):
                    return None, message
            return None, "MANIFEST_SCHEMA_INVALID"
        message = str(error)
        return None, message if message.startswith("MANIFEST_") else "MANIFEST_JSON_INVALID"
    if not verify(manifest, public_keys, allow_test_keys=allow_test_keys):
        return None, "MANIFEST_SIGNATURE_INVALID"
    if expected_primitives_version is not None and manifest.primitives_version != (
        expected_primitives_version
    ):
        return None, "MANIFEST_PRIMITIVES_VERSION"
    if expected_parity_sha256 is not None and manifest.primitives_parity_sha256 != (
        expected_parity_sha256
    ):
        return None, "MANIFEST_PRIMITIVES_PARITY"
    return manifest, "MANIFEST_ACCEPTED"
