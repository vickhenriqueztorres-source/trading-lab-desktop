"""R-MAN-1..7: only public offline fixtures."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def fixture_document():
    return json.loads((ROOT / "tests/fixtures/manifest_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def test_seed():
    return bytes.fromhex((ROOT / "tests/keys/ed25519-test.seed.hex").read_text().strip())


@pytest.fixture
def test_public():
    return bytes.fromhex((ROOT / "tests/keys/ed25519-test.public.hex").read_text().strip())
