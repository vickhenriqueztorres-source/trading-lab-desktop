from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.auth_agent.pinned_keys import PINNED_LEASE_KEYS
from packages.licensing.lease import LeaseVerifier
from packages.licensing.product_key import decode_product_key
from scripts.admin_panel import app, init_db


@pytest.fixture
def client(tmp_path: Path):
    db_file = tmp_path / "test_licenses.db"
    init_db(db_file)

    # Monkeypatch DB_PATH in scripts.admin_panel
    import scripts.admin_panel as ap

    orig_db = ap.DB_PATH
    ap.DB_PATH = db_file
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        ap.DB_PATH = orig_db


def test_index_page(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "TRADING" in res.text
    assert "Painel de Gestão de Licenças" in res.text


def test_create_and_verify_license(client: TestClient) -> None:
    # 1. Create a PRO license
    payload = {
        "client_name": "Teste Admin Visual",
        "client_contact": "+55 11 98888-7777",
        "plan": "PRO",
        "days": 30,
        "is_lifetime": False,
        "brokers": "DERIV,IQ_OPTION",
        "device_id": "*",
    }
    res = client.post("/api/licenses", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["client_name"] == "Teste Admin Visual"
    assert data["plan"] == "PRO"
    assert data["days"] == 30
    assert data["product_key"].startswith("TLKEY-PRO-")

    # 2. Cryptographically verify the product key with LeaseVerifier
    signed = decode_product_key(data["product_key"])
    pub_bytes = base64.urlsafe_b64decode(PINNED_LEASE_KEYS["tl-master-offline"].encode("ascii"))
    verifier = LeaseVerifier({"tl-master-offline": pub_bytes}, allow_long_term=True)
    claims = verifier.verify(signed)
    decision = verifier.evaluate(
        signed,
        now=claims.issued_at,
        expected_user_id="Teste Admin Visual",
        expected_device_id="hardware-pc-123",
        client_version="1.9.11",
        broker="DERIV",
        strategy_pack="core",
        real_mode=True,
    )
    assert decision.new_entries_allowed is True
    assert decision.reason.value == "AUTHORIZED"


def test_list_and_stats(client: TestClient) -> None:
    # Initially empty or with previous tests
    res0 = client.get("/api/licenses")
    assert res0.status_code == 200
    initial_count = res0.json()["stats"]["total"]

    # Create 2 licenses (1 PRO 30d, 1 DEMO lifetime)
    client.post(
        "/api/licenses",
        json={"client_name": "Cliente Um", "plan": "PRO", "days": 30},
    )
    client.post(
        "/api/licenses",
        json={"client_name": "Cliente Dois", "plan": "DEMO", "is_lifetime": True, "days": 36500},
    )

    res = client.get("/api/licenses")
    assert res.status_code == 200
    body = res.json()
    assert body["stats"]["total"] == initial_count + 2
    assert body["stats"]["active"] >= 2
    assert body["stats"]["lifetime"] >= 1

    names = [lic["client_name"] for lic in body["licenses"]]
    assert "Cliente Um" in names
    assert "Cliente Dois" in names


def test_download_and_delete_license(client: TestClient) -> None:
    res = client.post(
        "/api/licenses",
        json={"client_name": "Para Deletar", "plan": "PRO", "days": 7},
    )
    lic_id = res.json()["id"]
    product_key = res.json()["product_key"]

    # Download
    dl_res = client.get(f"/api/licenses/{lic_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.text.strip() == product_key.strip()

    # Delete
    del_res = client.delete(f"/api/licenses/{lic_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"

    # Confirm it's no longer listed
    list_res = client.get("/api/licenses")
    ids = [lic["id"] for lic in list_res.json()["licenses"]]
    assert lic_id not in ids


def test_validation_error(client: TestClient) -> None:
    res = client.post("/api/licenses", json={"client_name": "   "})
    assert res.status_code == 400
