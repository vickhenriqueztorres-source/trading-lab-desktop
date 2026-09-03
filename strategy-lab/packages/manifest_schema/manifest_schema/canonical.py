"""R-MAN-4: canonical UTF-8 JSON; no floats, Unicode normalization or coercion."""

import json
from typing import Any

from manifest_schema.rules import MAX_SAFE_INTEGER

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32


def _check(value: object, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValueError("MANIFEST_TOO_DEEP")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("MANIFEST_INTEGER_RANGE")
        return
    if type(value) is str:
        value.encode("utf-8")
        return
    if type(value) is list:
        for item in value:
            _check(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("MANIFEST_KEY_TYPE")
            _check(key, depth + 1)
            _check(item, depth + 1)
        return
    raise ValueError("MANIFEST_NON_JSON_TYPE")


def canonical_bytes(manifest_dict_sem_signature: dict[str, Any]) -> bytes:
    if type(manifest_dict_sem_signature) is not dict:
        raise ValueError("MANIFEST_ROOT_TYPE")
    if "signature" in manifest_dict_sem_signature:
        raise ValueError("MANIFEST_SIGNATURE_MUST_BE_EXCLUDED")
    _check(manifest_dict_sem_signature)
    encoded = json.dumps(
        manifest_dict_sem_signature,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("MANIFEST_TOO_LARGE")
    return encoded


def _no_float(value: str) -> None:
    raise ValueError("MANIFEST_FLOAT_FORBIDDEN")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("MANIFEST_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def load_document(raw: bytes) -> dict[str, Any]:
    """Bounded ingestion also rejects duplicate keys and integral float tokens (1.0)."""
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("MANIFEST_TOO_LARGE")
    result: object = json.loads(
        raw.decode("utf-8"),
        parse_float=_no_float,
        parse_constant=_no_float,
        object_pairs_hook=_unique_pairs,
    )
    if type(result) is not dict:
        raise ValueError("MANIFEST_ROOT_TYPE")
    _check(result)
    return result
