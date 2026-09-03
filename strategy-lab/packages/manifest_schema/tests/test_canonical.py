"""R-MAN-1/4: no lossy JSON conversion or duplicate-key ambiguity."""

import json
from decimal import Decimal

import pytest
from manifest_schema.acceptance import evaluate
from manifest_schema.canonical import canonical_bytes, load_document


def test_canonical_order_unicode_and_roundtrip():
    """R-MAN-4: exact ordering, compactness and UTF-8; decimal spelling is authenticated."""
    one = {"z": ["2.0", 2, None, False], "a": "Ação"}
    two = {"a": "Ação", "z": ["2.0", 2, None, False]}
    assert canonical_bytes(one) == canonical_bytes(two)
    assert canonical_bytes(one) == '{"a":"Ação","z":["2.0",2,null,false]}'.encode()
    assert load_document(canonical_bytes(one)) == one
    assert canonical_bytes({"v": "2"}) != canonical_bytes({"v": "2.0"})


@pytest.mark.parametrize("bad", [1.0, float("nan"), float("inf"), Decimal("1"), (), b"bytes"])
def test_canonical_forbids_non_json_types(bad):
    """R-MAN-1/4: no float, Decimal conversion, tuple coercion, NaN or Infinity."""
    with pytest.raises(ValueError, match="MANIFEST_NON_JSON_TYPE"):
        canonical_bytes({"bad": bad})


def test_bounds_and_signature_exclusion():
    """R-MAN-4: canonicalization is bounded and never silently drops a signature."""
    for value, code in [
        ({"signature": ""}, "MANIFEST_SIGNATURE_MUST_BE_EXCLUDED"),
        ({1: "bad"}, "MANIFEST_KEY_TYPE"),
        ({"v": 9007199254740992}, "MANIFEST_INTEGER_RANGE"),
        ({"v": "a" * (4 * 1024 * 1024)}, "MANIFEST_TOO_LARGE"),
    ]:
        with pytest.raises(ValueError, match=code):
            canonical_bytes(value)
    nested = {"x": None}
    for _ in range(33):
        nested = {"x": nested}
    with pytest.raises(ValueError, match="MANIFEST_TOO_DEEP"):
        canonical_bytes(nested)
    with pytest.raises(ValueError):
        canonical_bytes({"x": "\ud800"})


@pytest.mark.parametrize(
    "raw",
    [
        b'{"n":1.0}',
        b'{"n":1e2}',
        b'{"n":NaN}',
        b'{"n":Infinity}',
        b'{"n":1,"n":2}',
        b'{"x":{"n":1,"n":2}}',
        b"[]",
        b"null",
        b"{",
        b'{"n":9007199254740992}',
        b"\xff",
    ],
)
def test_raw_reader_rejects_ambiguous_json(raw):
    """R-MAN-1/4: raw hostile lexemes rejected before any model or signature handling."""
    with pytest.raises((ValueError, UnicodeError)):
        load_document(raw)


def test_oversize_and_deep_ingestion():
    """R-MAN-4: hostile payload bounds cannot crash the public ingestion function."""
    assert evaluate(b" " * (4 * 1024 * 1024 + 1), {}) == (None, "MANIFEST_TOO_LARGE")
    raw = b'{"x":' * 40 + b"null" + b"}" * 40
    assert evaluate(raw, {}) == (None, "MANIFEST_TOO_DEEP")
    raw = b'{"x":' * 2000 + b"null" + b"}" * 2000
    assert evaluate(raw, {})[0] is None


def test_float_field_direct_model(fixture_document):
    """R-MAN-1: Pydantic input also rejects a float instead of a wire decimal string."""
    from manifest_schema.models import Manifest
    from pydantic import ValidationError

    fixture_document["strategies"][0]["validated"]["p_hat"] = 0.578
    with pytest.raises(ValidationError):
        Manifest.model_validate(fixture_document)
    assert evaluate(json.dumps(fixture_document).encode(), {})[1] == "MANIFEST_FLOAT_FORBIDDEN"
