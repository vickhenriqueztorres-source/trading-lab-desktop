from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from apps.launcher.updater_service import UpdateManager
from packages.security.integrity import ReleaseManifestBuilder
from packages.security.updater import (
    UpdatePackageSigner,
    UpdateVerificationError,
)


def _build_signed_package(
    target_zip: Path,
    files: dict[str, str],
    version: str,
    private_key: ed25519.Ed25519PrivateKey,
) -> tuple[Path, any]:
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = target_zip.parent / f"temp_{version}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for fname, content in files.items():
        p = temp_dir / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    manifest = ReleaseManifestBuilder.build_manifest(temp_dir, version)
    ReleaseManifestBuilder.write_manifest(manifest, temp_dir / "release_manifest.json")

    with zipfile.ZipFile(target_zip, "w") as zf:
        for p in temp_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(temp_dir).as_posix())

    signed_manifest = UpdatePackageSigner.sign_manifest(
        target_version=version,
        min_source_version="1.0.0",
        package_path=target_zip,
        private_key=private_key,
    )
    return target_zip, signed_manifest


def test_update_manager_successful_update(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("print('version 1.0.0')", encoding="utf-8")
    (app_dir / "state.db").write_text("DO_NOT_TOUCH_DB", encoding="utf-8")

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    pkg_zip, signed_manifest = _build_signed_package(
        tmp_path / "update_1.1.0.zip",
        {"main.py": "print('version 1.1.0')", "new_tool.py": "x = 42"},
        "1.1.0",
        priv_key,
    )

    updates_dir = tmp_path / "updates"

    success = UpdateManager.apply_signed_update(
        app_dir=app_dir,
        package_zip_path=pkg_zip,
        manifest=signed_manifest,
        public_key=pub_key,
        updates_dir=updates_dir,
        current_version="1.0.0",
    )

    assert success is True
    assert (app_dir / "main.py").read_text() == "print('version 1.1.0')"
    assert (app_dir / "new_tool.py").read_text() == "x = 42"
    assert (app_dir / "state.db").read_text() == "DO_NOT_TOUCH_DB"


def test_update_manager_automatic_rollback_on_health_check_failure(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("print('version 1.0.0')", encoding="utf-8")
    (app_dir / "state.db").write_text("DO_NOT_TOUCH_DB", encoding="utf-8")

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    pkg_zip, signed_manifest = _build_signed_package(
        tmp_path / "update_1.2.0.zip",
        {"main.py": "print('version 1.2.0-broken')"},
        "1.2.0",
        priv_key,
    )

    updates_dir = tmp_path / "updates"

    # Health check that fails intentionally
    def failing_health_check(target: Path) -> bool:
        return False

    with pytest.raises(UpdateVerificationError) as exc_info:
        UpdateManager.apply_signed_update(
            app_dir=app_dir,
            package_zip_path=pkg_zip,
            manifest=signed_manifest,
            public_key=pub_key,
            updates_dir=updates_dir,
            current_version="1.0.0",
            health_checker=failing_health_check,
        )

    assert exc_info.value.reason_code == "POST_UPDATE_HEALTH_CHECK_FAILED"

    # Verify rollback restored app_dir to original state and preserved DB
    assert (app_dir / "main.py").read_text() == "print('version 1.0.0')"
    assert (app_dir / "state.db").read_text() == "DO_NOT_TOUCH_DB"
