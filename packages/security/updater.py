from __future__ import annotations

import fnmatch
import hashlib
import shutil
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from packages.domain.canonical import canonical_bytes
from packages.domain.models import OrderState
from packages.security.integrity import ReleaseIntegrityVerifier

_HEX_CHARS = 64


class UpdateVerificationError(RuntimeError):
    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or f"Update verification error: {reason_code}")


class UpdateBlockedActiveExposureError(RuntimeError):
    reason_code = "UPDATE_BLOCKED_ACTIVE_EXPOSURE"

    def __init__(
        self,
        message: str = "Update blocked due to active financial exposure or orders",
    ) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SignedUpdateManifest:
    target_version: str
    min_source_version: str
    build_timestamp_utc: str
    package_sha256: str
    package_size_bytes: int
    release_notes: str
    signature_hex: str

    def __post_init__(self) -> None:
        if not self.target_version.strip() or "\x00" in self.target_version:
            raise ValueError("target_version is invalid")
        if not self.min_source_version.strip() or "\x00" in self.min_source_version:
            raise ValueError("min_source_version is invalid")
        if len(self.package_sha256) != _HEX_CHARS or any(
            c not in "0123456789abcdef" for c in self.package_sha256
        ):
            raise ValueError("package_sha256 must be a 64-char lowercase hex string")
        if self.package_size_bytes <= 0:
            raise ValueError("package_size_bytes must be positive")
        if len(self.signature_hex) != 128 or any(
            c not in "0123456789abcdef" for c in self.signature_hex.lower()
        ):
            raise ValueError("signature_hex must be a 128-char hex string (64 bytes Ed25519)")

    def canonical_bytes(self) -> bytes:
        unsigned = {
            "build_timestamp_utc": self.build_timestamp_utc,
            "min_source_version": self.min_source_version,
            "package_sha256": self.package_sha256.lower(),
            "package_size_bytes": self.package_size_bytes,
            "release_notes": self.release_notes,
            "target_version": self.target_version,
        }
        return canonical_bytes(unsigned)

    def to_payload(self) -> dict[str, object]:
        return {
            "build_timestamp_utc": self.build_timestamp_utc,
            "min_source_version": self.min_source_version,
            "package_sha256": self.package_sha256.lower(),
            "package_size_bytes": self.package_size_bytes,
            "release_notes": self.release_notes,
            "signature_hex": self.signature_hex.lower(),
            "target_version": self.target_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        raw_size = payload["package_size_bytes"]
        if not isinstance(raw_size, (int, str)):
            raise ValueError("package_size_bytes must be int or str")
        return cls(
            target_version=str(payload["target_version"]),
            min_source_version=str(payload["min_source_version"]),
            build_timestamp_utc=str(payload["build_timestamp_utc"]),
            package_sha256=str(payload["package_sha256"]).lower(),
            package_size_bytes=int(raw_size),
            release_notes=str(payload.get("release_notes", "")),
            signature_hex=str(payload["signature_hex"]).lower(),
        )


def _compute_file_sha256(file_path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


class UpdatePackageSigner:
    @classmethod
    def sign_manifest(
        cls,
        target_version: str,
        min_source_version: str,
        package_path: Path,
        private_key: ed25519.Ed25519PrivateKey,
        release_notes: str = "",
        build_timestamp_utc: str | None = None,
    ) -> SignedUpdateManifest:
        pkg = Path(package_path).resolve()
        if not pkg.is_file():
            raise ValueError(f"Package file not found: {pkg}")
        sha256, size_bytes = _compute_file_sha256(pkg)
        ts = (
            build_timestamp_utc
            if build_timestamp_utc is not None
            else datetime.now(UTC).isoformat()
        )
        unsigned_data = {
            "build_timestamp_utc": ts,
            "min_source_version": min_source_version,
            "package_sha256": sha256.lower(),
            "package_size_bytes": size_bytes,
            "release_notes": release_notes,
            "target_version": target_version,
        }
        to_sign = canonical_bytes(unsigned_data)
        signature = private_key.sign(to_sign)
        signature_hex = signature.hex()

        return SignedUpdateManifest(
            target_version=target_version,
            min_source_version=min_source_version,
            build_timestamp_utc=ts,
            package_sha256=sha256.lower(),
            package_size_bytes=size_bytes,
            release_notes=release_notes,
            signature_hex=signature_hex,
        )


class UpdateSignatureVerifier:
    @classmethod
    def verify_manifest(
        cls,
        manifest: SignedUpdateManifest,
        public_key: ed25519.Ed25519PublicKey | str,
    ) -> bool:
        if isinstance(public_key, str):
            try:
                key_bytes = bytes.fromhex(public_key)
                pub = ed25519.Ed25519PublicKey.from_public_bytes(key_bytes)
            except Exception:
                return False
        else:
            pub = public_key

        to_verify = manifest.canonical_bytes()
        try:
            signature_bytes = bytes.fromhex(manifest.signature_hex)
            pub.verify(signature_bytes, to_verify)
            return True
        except (InvalidSignature, ValueError):
            return False

    @classmethod
    def verify_package_file(cls, package_path: Path, expected_sha256: str) -> bool:
        pkg = Path(package_path).resolve()
        if not pkg.is_file():
            return False
        actual_sha256, _ = _compute_file_sha256(pkg)
        return actual_sha256.lower() == expected_sha256.lower()


class UpdateSafetyGuard:
    @classmethod
    def can_apply_update(cls, state_reader: Any) -> tuple[bool, str | None]:
        if state_reader is None:
            return True, None

        # 1. Check orders if method exists
        if hasattr(state_reader, "list_orders"):
            try:
                orders = state_reader.list_orders()
                for order in orders:
                    # An order is dangerous if its state is not terminal
                    state = getattr(order, "state", None)
                    if state is not None:
                        if isinstance(state, OrderState):
                            if not state.is_terminal:
                                return False, "UPDATE_BLOCKED_ACTIVE_EXPOSURE"
                        elif str(state) not in {
                            OrderState.SETTLED.value,
                            OrderState.REJECTED.value,
                        }:
                            return False, "UPDATE_BLOCKED_ACTIVE_EXPOSURE"
            except Exception:
                return False, "UPDATE_BLOCKED_ACTIVE_EXPOSURE"

        # 2. Check risk reservations if method exists
        if hasattr(state_reader, "list_active_reservations"):
            try:
                active_reservations = state_reader.list_active_reservations()
                if len(active_reservations) > 0:
                    return False, "UPDATE_BLOCKED_ACTIVE_EXPOSURE"
            except Exception:
                return False, "UPDATE_BLOCKED_ACTIVE_EXPOSURE"

        # 3. Check exposure ledger / locks if method exists
        if hasattr(state_reader, "get_global_exposure"):
            try:
                exposure = state_reader.get_global_exposure()
                if exposure and getattr(exposure, "active_minor_units", 0) > 0:
                    return False, "UPDATE_BLOCKED_ACTIVE_EXPOSURE"
            except Exception:
                pass

        return True, None

    @classmethod
    def ensure_safe_for_update(cls, state_reader: Any) -> None:
        can_apply, reason = cls.can_apply_update(state_reader)
        if not can_apply:
            raise UpdateBlockedActiveExposureError(
                f"Cannot apply update: {reason or 'UPDATE_BLOCKED_ACTIVE_EXPOSURE'}"
            )


class UpdateApplier:
    BACKUP_EXCLUDE_PATTERNS: tuple[str, ...] = (
        "*.db*",
        "*.vault",
        "*.log",
        "data/*",
        "reports/*",
        "updates/*",
        ".git*",
        "__pycache__",
        "*.pyc",
    )

    @classmethod
    def prepare_staging(
        cls,
        package_zip_path: Path,
        staging_dir: Path,
        manifest: SignedUpdateManifest,
    ) -> Path:
        pkg_path = Path(package_zip_path).resolve()
        stg_dir = Path(staging_dir).resolve()

        if not pkg_path.is_file():
            raise UpdateVerificationError(
                "PACKAGE_NOT_FOUND", f"Update package file not found: {pkg_path}"
            )

        if not UpdateSignatureVerifier.verify_package_file(pkg_path, manifest.package_sha256):
            raise UpdateVerificationError(
                "PACKAGE_SHA256_MISMATCH", "Package file SHA-256 does not match manifest"
            )

        if stg_dir.exists():
            shutil.rmtree(stg_dir)
        stg_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(pkg_path, "r") as zf:
            zf.extractall(stg_dir)

        # If staging contains release_manifest.json, self-verify
        manifest_file = stg_dir / "release_manifest.json"
        if manifest_file.is_file():
            res = ReleaseIntegrityVerifier.verify_distribution(stg_dir, manifest_file)
            if not res.is_valid:
                shutil.rmtree(stg_dir)
                raise UpdateVerificationError(
                    "STAGING_INTEGRITY_FAILED",
                    f"Staging package integrity verification failed: {res.issues}",
                )

        return stg_dir

    @classmethod
    def backup_current_version(
        cls,
        app_dir: Path,
        backup_dir: Path,
        current_version: str,
    ) -> Path:
        src = Path(app_dir).resolve()
        b_root = Path(backup_dir).resolve()
        target_backup = b_root / current_version

        if target_backup.exists():
            shutil.rmtree(target_backup)
        target_backup.mkdir(parents=True, exist_ok=True)

        for p in src.rglob("*"):
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                if any(
                    fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat)
                    for pat in cls.BACKUP_EXCLUDE_PATTERNS
                ):
                    continue
                dest = target_backup / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)

        return target_backup

    @classmethod
    def apply_update(cls, staging_dir: Path, app_dir: Path) -> None:
        stg = Path(staging_dir).resolve()
        app = Path(app_dir).resolve()

        if not stg.is_dir():
            raise ValueError(f"Staging directory not found: {stg}")

        for p in stg.rglob("*"):
            if p.is_file():
                rel = p.relative_to(stg).as_posix()
                dest = app / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)

    @classmethod
    def rollback(cls, backup_dir: Path, app_dir: Path) -> None:
        bak = Path(backup_dir).resolve()
        app = Path(app_dir).resolve()

        if not bak.is_dir():
            raise ValueError(f"Backup directory not found: {bak}")

        for p in bak.rglob("*"):
            if p.is_file():
                rel = p.relative_to(bak).as_posix()
                dest = app / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
