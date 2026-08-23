from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

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


def compile_executable(
    project_root: Path,
    output_dir: Path,
    version: str = "1.0.0",
    platform_name: str = "win64",
    skip_pyinstaller: bool = False,
) -> Path:
    root = Path(project_root).resolve()
    out = Path(output_dir).resolve()
    target_dist = out / "TradingLab"
    spec_file = root / "build_scripts" / "TradingLab.spec"

    print("==================================================================")
    print(f"TRADING LAB DESKTOP — COMPILATION PIPELINE (v{version})")
    print(f"Target distribution folder: {target_dist}")
    print("==================================================================")

    # 1. Run PyInstaller if requested
    if not skip_pyinstaller:
        print("[1/5] Running PyInstaller compilation...")
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(out),
            "--workpath",
            str(out / "build"),
            str(spec_file),
        ]
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            print("PyInstaller stdout:", result.stdout)
            print("PyInstaller stderr:", result.stderr, file=sys.stderr)
            raise RuntimeError(f"PyInstaller build failed with exit code {result.returncode}")
        print("PyInstaller build completed successfully.")
    else:
        print("[1/5] Skipping PyInstaller step (--skip-pyinstaller).")
        target_dist.mkdir(parents=True, exist_ok=True)
        # Staging fallback
        for folder in ("apps", "packages"):
            src_folder = root / folder
            dest_folder = target_dist / folder
            if src_folder.is_dir() and not dest_folder.exists():
                shutil.copytree(
                    src_folder,
                    dest_folder,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", "*.db*", "*.vault", ".env*", "*.log"
                    ),
                )
        exe_file = target_dist / "TradingLab.exe"
        if not exe_file.exists():
            exe_file.write_text("TRADING_LAB_BINARY_STUB", encoding="utf-8")

    # 2. Verify target executable exists
    exe_path = target_dist / "TradingLab.exe"
    if not exe_path.is_file():
        raise FileNotFoundError(f"Expected binary not found: {exe_path}")
    print(f"[2/5] Verified executable binary: {exe_path.name} ({exe_path.stat().st_size} bytes)")

    # 3. Security Scan with SecretScanner
    print("[3/5] Scanning distribution directory for secrets, credentials or database files...")
    scanner = SecretScanner()
    report = scanner.scan_directory(target_dist)
    if report.total_matches > 0:
        shutil.rmtree(target_dist, ignore_errors=True)
        raise RuntimeError(
            f"Security scan failed: {report.total_matches} forbidden secrets/files in distribution!"
        )
    print("Security scan clean (0 secrets detected).")

    # 4. Generate Release Manifest
    print("[4/5] Computing SHA-256 integrity hashes for all packaged files...")
    manifest = ReleaseManifestBuilder.build_manifest(
        root_dir=target_dist,
        version=version,
        platform=platform_name,
        exclude_patterns=EXCLUDE_PATTERNS,
    )
    manifest_path = target_dist / "release_manifest.json"
    ReleaseManifestBuilder.write_manifest(manifest, manifest_path)
    print(
        f"Generated release_manifest.json with {len(manifest.files)} files. "
        f"Manifest SHA-256: {manifest.manifest_hash}"
    )

    # 5. Self-verify Distribution Integrity
    print("[5/5] Performing self-verification of release package integrity...")
    verification = ReleaseIntegrityVerifier.verify_distribution(target_dist, manifest_path)
    if not verification.is_valid:
        shutil.rmtree(target_dist, ignore_errors=True)
        raise ReleaseIntegrityViolationError(verification.issues)

    if not skip_pyinstaller:
        print("[6/6] Running packaged launcher integrity health check...")
        try:
            smoke = subprocess.run(
                [str(exe_path), "--post-update-health-check"],
                cwd=target_dist,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Packaged launcher health check could not complete") from exc
        if smoke.returncode != 0:
            raise RuntimeError(
                f"Packaged launcher health check failed with exit code {smoke.returncode}"
            )

    print("==================================================================")
    print("BUILD SUCCESSFUL: Trading Lab Desktop distribution is ready!")
    print(f"Output folder: {target_dist}")
    print("==================================================================")
    return target_dist


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile Trading Lab Desktop Windows Executable")
    parser.add_argument("--version", default="1.0.0", help="Release version string")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist",
        help="Output directory",
    )
    parser.add_argument(
        "--skip-pyinstaller",
        action="store_true",
        help="Skip PyInstaller and stage directory directly",
    )
    args = parser.parse_args()

    try:
        compile_executable(
            project_root=PROJECT_ROOT,
            output_dir=args.output_dir,
            version=args.version,
            skip_pyinstaller=args.skip_pyinstaller,
        )
        return 0
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
