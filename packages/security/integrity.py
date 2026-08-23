from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

from packages.domain.canonical import canonical_bytes

_HEX_CHARS = 64


class IntegrityIssueType(StrEnum):
    HASH_MISMATCH = "HASH_MISMATCH"
    MISSING_FILE = "MISSING_FILE"
    UNTRACKED_FILE = "UNTRACKED_FILE"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    MANIFEST_CORRUPTED = "MANIFEST_CORRUPTED"


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    issue_type: IntegrityIssueType
    relative_path: str
    description: str


@dataclass(frozen=True, slots=True)
class IntegrityVerificationResult:
    is_valid: bool
    issues: tuple[IntegrityIssue, ...]


class ReleaseIntegrityViolationError(RuntimeError):
    reason_code = "INTEGRITY_CHECK_FAILED"

    def __init__(self, issues: Sequence[IntegrityIssue]) -> None:
        super().__init__(f"Release integrity check failed with {len(issues)} issues")
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class FileIntegrityRecord:
    relative_path: str
    sha256_hash: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.relative_path.strip() or "\\x00" in self.relative_path:
            raise ValueError("relative_path cannot be empty or contain null bytes")
        if len(self.sha256_hash) != _HEX_CHARS or any(
            c not in "0123456789abcdef" for c in self.sha256_hash
        ):
            raise ValueError("sha256_hash must be a 64-char lowercase hex string")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")

    def to_payload(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "sha256_hash": self.sha256_hash,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        raw_size = payload["size_bytes"]
        if not isinstance(raw_size, (int, str)):
            raise ValueError("size_bytes must be int or str")
        return cls(
            relative_path=str(payload["relative_path"]),
            sha256_hash=str(payload["sha256_hash"]).lower(),
            size_bytes=int(raw_size),
        )


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    version: str
    build_timestamp_utc: str
    platform: str
    files: Mapping[str, FileIntegrityRecord]
    manifest_hash: str

    def to_payload(self) -> dict[str, object]:
        files_payload = {
            rel_path: record.to_payload() for rel_path, record in sorted(self.files.items())
        }
        return {
            "build_timestamp_utc": self.build_timestamp_utc,
            "files": files_payload,
            "manifest_hash": self.manifest_hash,
            "platform": self.platform,
            "version": self.version,
        }

    def canonical_bytes(self) -> bytes:
        files_payload = {
            rel_path: record.to_payload() for rel_path, record in sorted(self.files.items())
        }
        unsigned = {
            "build_timestamp_utc": self.build_timestamp_utc,
            "files": files_payload,
            "platform": self.platform,
            "version": self.version,
        }
        return canonical_bytes(unsigned)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        files_raw = payload.get("files", {})
        if not isinstance(files_raw, dict):
            raise ValueError("files must be a dictionary")
        files_map: dict[str, FileIntegrityRecord] = {}
        for rel_path, record_payload in files_raw.items():
            if isinstance(record_payload, Mapping):
                files_map[str(rel_path)] = FileIntegrityRecord.from_payload(record_payload)
            else:
                raise ValueError("file record must be a mapping")
        version = str(payload["version"])
        build_timestamp_utc = str(payload["build_timestamp_utc"])
        platform = str(payload["platform"])
        manifest_hash = str(payload["manifest_hash"])
        return cls(
            version=version,
            build_timestamp_utc=build_timestamp_utc,
            platform=platform,
            files=files_map,
            manifest_hash=manifest_hash,
        )


def _compute_sha256_streaming(file_path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total_size = 0
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            total_size += len(chunk)
    return hasher.hexdigest(), total_size


def _matches_any_pattern(relative_path_str: str, patterns: Sequence[str]) -> bool:
    normalized = relative_path_str.replace("\\", "/")
    for pattern in patterns:
        norm_pattern = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normalized, norm_pattern) or fnmatch.fnmatch(
            Path(normalized).name, norm_pattern
        ):
            return True
    return False


class ReleaseManifestBuilder:
    DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
        "*.pyc",
        "__pycache__",
        "*.db*",
        "*.vault",
        ".git*",
        "tests/*",
        ".env*",
        "release_manifest.json",
    )

    @classmethod
    def build_manifest(
        cls,
        root_dir: Path,
        version: str,
        platform: str = "windows_x86_64",
        build_timestamp_utc: str | None = None,
        exclude_patterns: Sequence[str] = DEFAULT_EXCLUDE_PATTERNS,
    ) -> ReleaseManifest:
        root = Path(root_dir).resolve()
        if not root.is_dir():
            raise ValueError(f"root_dir must be an existing directory: {root}")

        ts = (
            build_timestamp_utc
            if build_timestamp_utc is not None
            else datetime.now(UTC).isoformat()
        )

        files_map: dict[str, FileIntegrityRecord] = {}

        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel_path = p.relative_to(root).as_posix()
                if _matches_any_pattern(rel_path, exclude_patterns):
                    continue
                sha256, size_bytes = _compute_sha256_streaming(p)
                files_map[rel_path] = FileIntegrityRecord(
                    relative_path=rel_path,
                    sha256_hash=sha256,
                    size_bytes=size_bytes,
                )

        unsigned_data = {
            "build_timestamp_utc": ts,
            "files": {k: v.to_payload() for k, v in sorted(files_map.items())},
            "platform": platform,
            "version": version,
        }
        manifest_hash = hashlib.sha256(canonical_bytes(unsigned_data)).hexdigest()

        return ReleaseManifest(
            version=version,
            build_timestamp_utc=ts,
            platform=platform,
            files=files_map,
            manifest_hash=manifest_hash,
        )

    @classmethod
    def write_manifest(cls, manifest: ReleaseManifest, target_path: Path) -> None:
        target = Path(target_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = manifest.to_payload()
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class ReleaseIntegrityVerifier:
    DEFAULT_EXCLUDE_UNTRACKED_PATTERNS: tuple[str, ...] = (
        "release_manifest.json",
        "*.log",
        "data/*",
        "reports/*",
        "*.db*",
        "*.vault",
        "__pycache__",
        "*.pyc",
    )

    @classmethod
    def verify_distribution(
        cls,
        root_dir: Path,
        manifest_path: Path,
        exclude_untracked_patterns: Sequence[str] = DEFAULT_EXCLUDE_UNTRACKED_PATTERNS,
    ) -> IntegrityVerificationResult:
        root = Path(root_dir).resolve()
        manifest_file = Path(manifest_path).resolve()

        issues: list[IntegrityIssue] = []

        if not manifest_file.is_file():
            issues.append(
                IntegrityIssue(
                    issue_type=IntegrityIssueType.MANIFEST_CORRUPTED,
                    relative_path=manifest_file.name,
                    description="Release manifest file does not exist",
                )
            )
            return IntegrityVerificationResult(is_valid=False, issues=tuple(issues))

        try:
            content = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest = ReleaseManifest.from_payload(content)
        except Exception as exc:
            issues.append(
                IntegrityIssue(
                    issue_type=IntegrityIssueType.MANIFEST_CORRUPTED,
                    relative_path=manifest_file.name,
                    description=f"Release manifest is malformed or corrupted: {exc}",
                )
            )
            return IntegrityVerificationResult(is_valid=False, issues=tuple(issues))

        # Verify manifest hash self-consistency
        unsigned_bytes = manifest.canonical_bytes()
        expected_manifest_hash = hashlib.sha256(unsigned_bytes).hexdigest()
        if expected_manifest_hash != manifest.manifest_hash:
            issues.append(
                IntegrityIssue(
                    issue_type=IntegrityIssueType.MANIFEST_CORRUPTED,
                    relative_path=manifest_file.name,
                    description="Manifest self-integrity hash mismatch",
                )
            )

        # 1. Check all manifest files against disk
        for rel_path, record in manifest.files.items():
            disk_file = root / rel_path
            if not disk_file.is_file():
                issues.append(
                    IntegrityIssue(
                        issue_type=IntegrityIssueType.MISSING_FILE,
                        relative_path=rel_path,
                        description="File listed in release manifest is missing on disk",
                    )
                )
                continue

            actual_hash, actual_size = _compute_sha256_streaming(disk_file)
            if actual_size != record.size_bytes:
                issues.append(
                    IntegrityIssue(
                        issue_type=IntegrityIssueType.SIZE_MISMATCH,
                        relative_path=rel_path,
                        description=(
                            f"File size mismatch: expected {record.size_bytes} bytes, "
                            f"got {actual_size} bytes"
                        ),
                    )
                )
            elif actual_hash != record.sha256_hash:
                issues.append(
                    IntegrityIssue(
                        issue_type=IntegrityIssueType.HASH_MISMATCH,
                        relative_path=rel_path,
                        description=(
                            f"File SHA-256 mismatch: expected {record.sha256_hash}, "
                            f"got {actual_hash}"
                        ),
                    )
                )

        # 2. Check for unauthorized untracked files on disk
        for p in root.rglob("*"):
            if p.is_file():
                rel_path = p.relative_to(root).as_posix()
                if _matches_any_pattern(rel_path, exclude_untracked_patterns):
                    continue
                if rel_path not in manifest.files:
                    issues.append(
                        IntegrityIssue(
                            issue_type=IntegrityIssueType.UNTRACKED_FILE,
                            relative_path=rel_path,
                            description="Unauthorized file found on disk not present in manifest",
                        )
                    )

        is_valid = len(issues) == 0
        return IntegrityVerificationResult(is_valid=is_valid, issues=tuple(issues))
