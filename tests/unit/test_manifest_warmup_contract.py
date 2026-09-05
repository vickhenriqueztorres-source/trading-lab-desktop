"""Additive v1.1 signed wire acceptance without imports from Strategy Lab."""

from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.core.manifest_catalog import DynamicManifestCatalog
from apps.core.manifest_client import Accepted, ManifestClient, Rejected
from tests.unit.test_manifest_client_hostile import VALID_DOC_BASE, MockClock, _sign_document


def test_signed_v11_rejects_only_entry_with_wrong_warmup(tmp_path: Path) -> None:
    doc = deepcopy(VALID_DOC_BASE)
    doc["schema_revision"] = "1.1"
    doc["strategies"][0]["warmup_required"] = 28
    bad = deepcopy(doc["strategies"][0])
    bad["key"] = "f1:bad-warmup"
    bad["warmup_required"] = 27
    doc["strategies"].append(bad)
    key = Ed25519PrivateKey.generate()
    client = ManifestClient(
        clock=MockClock(),
        cache_dir=tmp_path,
        public_keys={"A": key.public_key().public_bytes_raw()},
    )
    assert isinstance(client.accept(_sign_document(doc, key)), Accepted)
    catalog = DynamicManifestCatalog()
    catalog.apply_manifest(client.current())
    assert set(catalog.active_strategies) == {doc["strategies"][0]["key"]}
    assert catalog.events[-1][0] == "WARMUP_MISMATCH"


@pytest.mark.parametrize("value", [None, True, "28", 0, 10001])
def test_signed_v11_requires_bounded_integer_warmup(tmp_path: Path, value: object) -> None:
    doc = deepcopy(VALID_DOC_BASE)
    doc["schema_revision"] = "1.1"
    doc["strategies"][0]["warmup_required"] = value
    key = Ed25519PrivateKey.generate()
    client = ManifestClient(
        clock=MockClock(),
        cache_dir=tmp_path,
        public_keys={"A": key.public_key().public_bytes_raw()},
    )
    assert isinstance(client.accept(_sign_document(doc, key)), Rejected)
