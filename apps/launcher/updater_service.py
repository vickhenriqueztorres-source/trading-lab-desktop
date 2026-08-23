from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

from packages.security.integrity import ReleaseIntegrityVerifier
from packages.security.updater import (
    SignedUpdateManifest,
    UpdateApplier,
    UpdateSafetyGuard,
    UpdateSignatureVerifier,
    UpdateVerificationError,
)


class UpdateManager:
    @classmethod
    def apply_signed_update(
        cls,
        app_dir: Path,
        package_zip_path: Path,
        manifest: SignedUpdateManifest,
        public_key: str | ed25519.Ed25519PublicKey,
        updates_dir: Path,
        current_version: str,
        state_reader: Any = None,
        health_checker: Callable[[Path], bool] | None = None,
    ) -> bool:
        app = Path(app_dir).resolve()
        pkg = Path(package_zip_path).resolve()
        upd = Path(updates_dir).resolve()

        # 1. Cryptographic signature check
        if not UpdateSignatureVerifier.verify_manifest(manifest, public_key):
            raise UpdateVerificationError(
                "UPDATE_SIGNATURE_INVALID", "Update manifest digital signature is invalid"
            )

        # 2. Package SHA-256 verification
        if not UpdateSignatureVerifier.verify_package_file(pkg, manifest.package_sha256):
            raise UpdateVerificationError(
                "PACKAGE_SHA256_MISMATCH", "Update package hash does not match manifest"
            )

        # 3. Core Safety / Active exposure check
        UpdateSafetyGuard.ensure_safe_for_update(state_reader)

        # 4. Create snapshot backup of current version
        backup_root = upd / "backup"
        backup_path = UpdateApplier.backup_current_version(app, backup_root, current_version)

        # 5. Extract and prepare staging
        staging_root = upd / "staging" / manifest.target_version
        staging_path = UpdateApplier.prepare_staging(pkg, staging_root, manifest)

        # 6. Apply update files
        UpdateApplier.apply_update(staging_path, app)

        # 7. Post-update health check
        healthy = False
        try:
            if health_checker is not None:
                healthy = bool(health_checker(app))
            else:
                manifest_file = app / "release_manifest.json"
                if manifest_file.is_file():
                    res = ReleaseIntegrityVerifier.verify_distribution(app, manifest_file)
                    healthy = res.is_valid
                else:
                    healthy = True
        except Exception:
            healthy = False

        # 8. Rollback if post-update health check failed
        if not healthy:
            UpdateApplier.rollback(backup_path, app)
            shutil.rmtree(staging_path, ignore_errors=True)
            raise UpdateVerificationError(
                "POST_UPDATE_HEALTH_CHECK_FAILED",
                "Post-update health check failed; safely rolled back to original version",
            )

        # 9. Clean staging on success
        shutil.rmtree(staging_path, ignore_errors=True)
        return True
