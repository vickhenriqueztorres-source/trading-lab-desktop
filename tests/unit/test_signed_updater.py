from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from packages.domain.models import OrderState, RiskReservationState
from packages.security.updater import (
    SignedUpdateManifest,
    UpdateApplier,
    UpdateBlockedActiveExposureError,
    UpdatePackageSigner,
    UpdateSafetyGuard,
    UpdateSignatureVerifier,
)


def _create_sample_zip(target_zip: Path, files: dict[str, str]) -> Path:
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip, "w") as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return target_zip


def test_ed25519_manifest_signing_and_verification(tmp_path: Path) -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes_raw().hex()

    pkg_zip = _create_sample_zip(tmp_path / "update.zip", {"app.py": "print('v2')"})

    manifest = UpdatePackageSigner.sign_manifest(
        target_version="2.0.0",
        min_source_version="1.0.0",
        package_path=pkg_zip,
        private_key=priv_key,
        release_notes="Bugfixes and improvements",
    )

    # Valid signature
    assert UpdateSignatureVerifier.verify_manifest(manifest, pub_key) is True
    assert UpdateSignatureVerifier.verify_manifest(manifest, pub_hex) is True

    # Forged public key
    other_priv = ed25519.Ed25519PrivateKey.generate()
    assert UpdateSignatureVerifier.verify_manifest(manifest, other_priv.public_key()) is False

    # Tampered manifest
    tampered_manifest = SignedUpdateManifest(
        target_version="2.0.1",
        min_source_version=manifest.min_source_version,
        build_timestamp_utc=manifest.build_timestamp_utc,
        package_sha256=manifest.package_sha256,
        package_size_bytes=manifest.package_size_bytes,
        release_notes=manifest.release_notes,
        signature_hex=manifest.signature_hex,
    )
    assert UpdateSignatureVerifier.verify_manifest(tampered_manifest, pub_key) is False


def test_package_file_sha256_verification(tmp_path: Path) -> None:
    pkg_zip = _create_sample_zip(tmp_path / "update.zip", {"app.py": "print('v2')"})
    priv_key = ed25519.Ed25519PrivateKey.generate()

    manifest = UpdatePackageSigner.sign_manifest(
        target_version="2.0.0",
        min_source_version="1.0.0",
        package_path=pkg_zip,
        private_key=priv_key,
    )

    assert UpdateSignatureVerifier.verify_package_file(pkg_zip, manifest.package_sha256) is True
    assert UpdateSignatureVerifier.verify_package_file(pkg_zip, "0" * 64) is False


@dataclass(frozen=True, slots=True)
class MockOrder:
    order_id: str
    state: OrderState


@dataclass(frozen=True, slots=True)
class MockReservation:
    reservation_id: str
    state: RiskReservationState


class MockStateReader:
    def __init__(
        self,
        orders: list[MockOrder] | None = None,
        reservations: list[MockReservation] | None = None,
    ) -> None:
        self._orders = orders or []
        self._reservations = reservations or []

    def list_orders(self) -> list[MockOrder]:
        return self._orders

    def list_active_reservations(self) -> list[MockReservation]:
        return [r for r in self._reservations if r.state == RiskReservationState.ACTIVE]


def test_update_safety_guard_blocks_when_active_orders_exist() -> None:
    # 1. Active open order
    reader_open = MockStateReader(orders=[MockOrder("ord-1", OrderState.OPEN)])
    can_apply, reason = UpdateSafetyGuard.can_apply_update(reader_open)
    assert can_apply is False
    assert reason == "UPDATE_BLOCKED_ACTIVE_EXPOSURE"

    with pytest.raises(UpdateBlockedActiveExposureError):
        UpdateSafetyGuard.ensure_safe_for_update(reader_open)

    # 2. Unknown order state
    reader_unknown = MockStateReader(orders=[MockOrder("ord-2", OrderState.UNKNOWN)])
    can_apply, reason = UpdateSafetyGuard.can_apply_update(reader_unknown)
    assert can_apply is False

    # 3. Active reservation
    reader_res = MockStateReader(
        reservations=[MockReservation("res-1", RiskReservationState.ACTIVE)]
    )
    can_apply, reason = UpdateSafetyGuard.can_apply_update(reader_res)
    assert can_apply is False

    # 4. Safe state: empty or only terminal orders
    reader_safe = MockStateReader(
        orders=[
            MockOrder("ord-1", OrderState.SETTLED),
            MockOrder("ord-2", OrderState.REJECTED),
        ],
        reservations=[
            MockReservation("res-1", RiskReservationState.RELEASED),
        ],
    )
    can_apply, reason = UpdateSafetyGuard.can_apply_update(reader_safe)
    assert can_apply is True
    assert reason is None
    UpdateSafetyGuard.ensure_safe_for_update(reader_safe)  # does not raise


def test_update_applier_staging_backup_and_rollback(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("v1.0.0", encoding="utf-8")
    (app_dir / "state.db").write_text("critical_finance_db", encoding="utf-8")
    (app_dir / "keys.vault").write_text("critical_vault", encoding="utf-8")

    pkg_zip = _create_sample_zip(
        tmp_path / "pkg.zip",
        {"main.py": "v2.0.0", "module.py": "helper"},
    )
    priv_key = ed25519.Ed25519PrivateKey.generate()
    manifest = UpdatePackageSigner.sign_manifest("2.0.0", "1.0.0", pkg_zip, priv_key)

    # Staging
    stg_dir = tmp_path / "staging"
    UpdateApplier.prepare_staging(pkg_zip, stg_dir, manifest)
    assert (stg_dir / "main.py").read_text() == "v2.0.0"

    # Backup
    backup_dir = tmp_path / "backup"
    backup_path = UpdateApplier.backup_current_version(app_dir, backup_dir, "1.0.0")
    assert (backup_path / "main.py").read_text() == "v1.0.0"
    assert not (backup_path / "state.db").exists()  # DB excluded from backup
    assert not (backup_path / "keys.vault").exists()  # Vault excluded from backup

    # Apply
    UpdateApplier.apply_update(stg_dir, app_dir)
    assert (app_dir / "main.py").read_text() == "v2.0.0"
    assert (app_dir / "module.py").read_text() == "helper"
    assert (app_dir / "state.db").read_text() == "critical_finance_db"  # Untouched

    # Rollback
    UpdateApplier.rollback(backup_path, app_dir)
    assert (app_dir / "main.py").read_text() == "v1.0.0"
    assert (app_dir / "state.db").read_text() == "critical_finance_db"
