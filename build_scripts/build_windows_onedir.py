from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.security.integrity import (  # noqa: E402
    ReleaseIntegrityVerifier,
    ReleaseIntegrityViolationError,
    ReleaseManifestBuilder,
)
from packages.security.secret_scanner import SecretScanner  # noqa: E402

EXCLUDE_PATTERNS = (
    "*.pyc",
    "__pycache__",
    "*.db*",
    "*.vault",
    ".git*",
    "tests/*",
    ".env*",
    "*.log",
    "reports/*",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist/*",
    "release_manifest.json",
)


def build_onedir_package(
    source_root: Path,
    output_dir: Path,
    version: str = "1.9.11",
    platform_name: str = "windows_x86_64",
) -> Path:
    src = Path(source_root).resolve()
    out = Path(output_dir).resolve()
    target_dist = out / "DualTrade"

    print(f"Building Windows Onedir package in: {target_dist}")

    # 1. Clean previous build
    if target_dist.exists():
        shutil.rmtree(target_dist)
    target_dist.mkdir(parents=True, exist_ok=True)

    # 2. Copy apps/ and packages/
    for folder in ("apps", "packages"):
        src_folder = src / folder
        if src_folder.is_dir():
            dest_folder = target_dist / folder
            shutil.copytree(
                src_folder,
                dest_folder,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.db*", "*.vault", ".env*", "*.log"
                ),
            )

    # 3. Copy top-level metadata files if present
    for fname in ("pyproject.toml", "README.md", "LICENSE"):
        fpath = src / fname
        if fpath.is_file():
            shutil.copy2(fpath, target_dist / fname)

    # 4. Create launcher wrapper DualTrade.bat / runner
    launcher_script = target_dist / "DualTrade.bat"
    launcher_script.write_text("@echo off\r\npython -m apps.launcher %*\r\n", encoding="utf-8")

    # 5. Security scan on distribution folder (Fail-Closed)
    print("Running SecretScanner on staged package...")
    scanner = SecretScanner()
    report = scanner.scan_directory(target_dist)
    if report.total_matches > 0:
        shutil.rmtree(target_dist)
        raise RuntimeError(
            f"Security scan failed: {report.total_matches} secrets detected in distribution bundle!"
        )
    print("Secret scan clean.")

    # 6. Generate and write release manifest
    print("Generating release_manifest.json...")
    manifest = ReleaseManifestBuilder.build_manifest(
        root_dir=target_dist,
        version=version,
        platform=platform_name,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    manifest_path = target_dist / "release_manifest.json"
    ReleaseManifestBuilder.write_manifest(manifest, manifest_path)
    print(
        f"Generated manifest with {len(manifest.files)} files. "
        f"Manifest SHA-256: {manifest.manifest_hash}"
    )

    # 7. Self-verify package integrity
    print("Verifying staged package integrity...")
    verification = ReleaseIntegrityVerifier.verify_distribution(target_dist, manifest_path)
    if not verification.is_valid:
        shutil.rmtree(target_dist)
        raise ReleaseIntegrityViolationError(verification.issues)

    print("Windows Onedir package built and verified successfully!")
    return target_dist


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Windows Onedir distribution package for DualTrade Desktop"
    )
    parser.add_argument(
        "--version",
        default="1.9.11",
        help="Release version string",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist",
        help="Output distribution root directory",
    )
    args = parser.parse_args()

    try:
        build_onedir_package(PROJECT_ROOT, args.output_dir, version=args.version)
        return 0
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
