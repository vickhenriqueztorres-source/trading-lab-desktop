from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSC_EXE = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
LAUNCHER_CS = PROJECT_ROOT / "build_scripts" / "PortableLauncher.cs"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def package_portable(
    source_dist: Path,
    target_dir: Path,
    exe_name: str = "TradingLab-Desktop-v1.9.11-RESILIENCE.exe",
) -> int:
    source = Path(source_dist).resolve()
    target = Path(target_dir).resolve()
    payload_zip = target / "TradingLab.payload.zip"
    target_exe = target / exe_name

    print("==================================================================")
    print("PORTABLE LAUNCHER COMPILATION")
    print(f"Source:  {source}")
    print(f"Target:  {target}")
    print(f"Output:  {target_exe}")
    print("==================================================================")

    if not source.is_dir():
        print(f"Error: Source distribution not found at {source}", file=sys.stderr)
        return 1

    target.mkdir(parents=True, exist_ok=True)

    if payload_zip.exists():
        payload_zip.unlink()

    print("[1/3] Creating payload zip archive...")
    shutil.make_archive(
        str(target / "TradingLab.payload"),
        "zip",
        root_dir=str(source.parent),
        base_dir=source.name,
    )
    zip_size = payload_zip.stat().st_size
    print(f"Payload archive created: {payload_zip.name} ({zip_size} bytes)")

    print("[2/3] Compiling standalone executable with csc.exe...")
    cmd = [
        str(CSC_EXE),
        "/target:winexe",
        "/optimize+",
        "/platform:x64",
        "/r:System.IO.Compression.FileSystem.dll",
        "/r:System.Windows.Forms.dll",
        f"/resource:{payload_zip},TradingLab.payload.zip",
        f"/out:{target_exe}",
        str(LAUNCHER_CS),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("csc stdout:", result.stdout)
        print("csc stderr:", result.stderr, file=sys.stderr)
        return result.returncode

    exe_size = target_exe.stat().st_size
    exe_hash = sha256_file(target_exe)

    print("[3/3] Standalone executable compiled successfully.")
    print("==================================================================")
    print("RELEASE ARTIFACT DETAILS:")
    print(f"Executable:  {target_exe}")
    print(f"File Size:   {exe_size} bytes ({exe_size / (1024 * 1024):.2f} MB)")
    print(f"SHA-256:     {exe_hash}")
    print("==================================================================")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package Trading Lab Desktop into a single portable EXE"
    )
    parser.add_argument(
        "--source-dist",
        type=Path,
        default=Path("C:/tlb_resilience_build/TradingLab"),
        help="Path to compiled onedir TradingLab folder",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=PROJECT_ROOT / "dist" / "resilience-v1.9.11",
        help="Target directory for portable package and zip",
    )
    parser.add_argument(
        "--exe-name",
        type=str,
        default="TradingLab-Desktop-v1.9.11-RESILIENCE.exe",
        help="Name of the final portable executable",
    )
    args = parser.parse_args()
    return package_portable(args.source_dist, args.target_dir, args.exe_name)


if __name__ == "__main__":
    raise SystemExit(main())
