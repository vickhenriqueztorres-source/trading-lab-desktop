"""R-MAN-4: signature, canonical bytes and production test-key rejection."""

import base64
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from manifest_schema.canonical import canonical_bytes
from manifest_schema.models import Manifest
from manifest_schema.signing import sign, unsigned_document, verify
from pydantic import ValidationError


@pytest.mark.parametrize("key_id", ["A", "B"])
def test_sign_verify_roundtrip(fixture_document, test_seed, test_public, key_id):
    """R-MAN-4: signed key ID and UTF-8 round-trip with public test fixture."""
    result = sign(fixture_document, test_seed, key_id, allow_test_keys=True)
    assert verify(result, {key_id: test_public}, allow_test_keys=True)
    assert verify(
        result.model_dump(exclude_unset=True), {key_id: test_public}, allow_test_keys=True
    )
    key = Ed25519PrivateKey.from_private_bytes(test_seed)
    key.public_key().verify(
        base64.b64decode(result.signature.removeprefix("ed25519:")),
        canonical_bytes(unsigned_document(result)),
    )
    assert fixture_document["key_id"] == "A"


def test_production_never_accepts_public_test_key(fixture_document, test_seed, test_public):
    """R-MAN-4 / I-8: relabeling the public test key as B cannot promote it to production."""
    for key_id in ("A", "B"):
        with pytest.raises(ValueError, match="MANIFEST_TEST_KEY_FORBIDDEN"):
            sign(fixture_document, test_seed, key_id)
        candidate = sign(fixture_document, test_seed, key_id, allow_test_keys=True)
        assert not verify(candidate, {key_id: test_public})


def test_fresh_in_memory_key_and_unknown_key(fixture_document):
    """R-MAN-4: ephemeral test key is never written; normal trust verification remains usable."""
    private = Ed25519PrivateKey.generate()
    signed = sign(fixture_document, private.private_bytes_raw(), "B")
    assert verify(signed, {"B": private.public_key().public_bytes_raw()})
    assert not verify(signed, {})
    assert not verify(signed, {"B": b"short"})
    assert not verify(signed, {"B": bytes(32)})


def test_mutated_nested_model_is_revalidated(fixture_document, test_seed, test_public):
    """R-MAN-3/4: frozen outer model is not permission to trust mutable nested dictionaries."""
    signed = sign(fixture_document, test_seed, "A", allow_test_keys=True)
    signed.strategies[0].params["bb_len"] = "999"
    assert not verify(signed, {"A": test_public}, allow_test_keys=True)
    with pytest.raises(ValidationError):
        sign(signed, test_seed, "A", allow_test_keys=True)


def test_signature_tamper_every_byte(fixture_document, test_public):
    """R-MAN-4: each of 64 individual signature bytes is tampered independently."""
    original = base64.b64decode(fixture_document["signature"].split(":", 1)[1])
    for index in range(64):
        changed = bytearray(original)
        changed[index] ^= 1
        doc = deepcopy(fixture_document)
        doc["signature"] = "ed25519:" + base64.b64encode(changed).decode()
        assert not verify(doc, {"A": test_public}, allow_test_keys=True)


@pytest.mark.parametrize("signature", ["ed25519:!", "ed25519:AA==", "", "wrong"])
def test_invalid_signature_shapes(fixture_document, test_public, signature):
    """R-MAN-4: malformed/empty signatures fail closed, no fallback key."""
    fixture_document["signature"] = signature
    assert not verify(fixture_document, {"A": test_public}, allow_test_keys=True)


def test_omitted_optional_field_is_not_inserted_into_signed_bytes(
    fixture_document,
    test_seed,
    test_public,
):
    """R-MAN-4: omission and explicit null are distinct signed representations."""
    model = Manifest.model_validate(fixture_document)
    assert "reason_pt" not in unsigned_document(model)["strategies"][0]
    fixture_document["strategies"][0]["reason_pt"] = None
    assert not verify(fixture_document, {"A": test_public}, allow_test_keys=True)
    signed = sign(fixture_document, test_seed, "A", allow_test_keys=True)
    assert verify(signed, {"A": test_public}, allow_test_keys=True)


def test_key_id_and_payload_are_authenticated(fixture_document, test_public):
    """R-MAN-4: identical public keys in A/B do not allow tampering with the signed selector."""
    fixture_document["key_id"] = "B"
    assert not verify(fixture_document, {"A": test_public, "B": test_public}, allow_test_keys=True)


def test_noncanonical_base64_padding_bits(fixture_document, test_public):
    """R-MAN-4: same decoded signature with invalid nonzero pad bits is rejected."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    signature = fixture_document["signature"]
    last = signature[-3]
    changed = alphabet[alphabet.index(last) + 1]
    fixture_document["signature"] = signature[:-3] + changed + "=="
    assert not verify(fixture_document, {"A": test_public}, allow_test_keys=True)
