from __future__ import annotations

from pathlib import Path

import pytest

from build_scripts.compile_trading_lab import compile_executable
from packages.security.integrity import ReleaseIntegrityVerifier


def test_build_spec_and_version_info_exist() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    spec_path = project_root / "build_scripts" / "TradingLab.spec"
    version_info_path = project_root / "build_scripts" / "version_info.txt"
    iss_path = project_root / "build_scripts" / "TradingLab_Setup.iss"

    assert spec_path.is_file(), "TradingLab.spec is missing"
    assert version_info_path.is_file(), "version_info.txt is missing"
    assert iss_path.is_file(), "TradingLab_Setup.iss is missing"

    spec_text = spec_path.read_text(encoding="utf-8")
    assert "TradingLab" in spec_text
    assert "apps" in spec_text
    assert "packages" in spec_text
    assert "PySide6" in spec_text
    assert "console=False" in spec_text
    assert 'FOREIGN_ICU_DLLS = {"icuuc.dll", "icudt78.dll"}' in spec_text

    compiler_text = (project_root / "build_scripts" / "compile_trading_lab.py").read_text(
        encoding="utf-8"
    )
    installer_text = iss_path.read_text(encoding="utf-8")
    assert "--post-update-health-check" in compiler_text
    assert "--post-update-health-check" in installer_text
    assert "ssPostInstall" in installer_text
    assert "GetCustomSetupExitCode" in installer_text
    assert "PostInstallHealthCheckFailed := True" in installer_text
    assert "UninstallFilesDir={localappdata}\\TradingLab\\uninstall" in installer_text

    version_text = version_info_path.read_text(encoding="utf-8")
    assert "Trading Lab Desktop" in version_text
    assert "1.9.11.0" in version_text


def test_compile_executable_staging_manifest_and_integrity(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    out_dir = tmp_path / "dist"

    target_dist = compile_executable(
        project_root=project_root,
        output_dir=out_dir,
        version="1.0.0",
        platform_name="win64",
        skip_pyinstaller=True,
    )

    assert target_dist.is_dir()
    assert (target_dist / "TradingLab.exe").is_file()
    assert (target_dist / "apps").is_dir()
    assert (target_dist / "packages").is_dir()

    manifest_file = target_dist / "release_manifest.json"
    assert manifest_file.is_file()

    # Self-verify distribution integrity
    verification = ReleaseIntegrityVerifier.verify_distribution(target_dist, manifest_file)
    assert verification.is_valid is True
    assert len(verification.issues) == 0


def test_compile_executable_fails_closed_on_secret_leak(tmp_path: Path) -> None:
    # Create fake project root with a leaked secret in apps/
    fake_root = tmp_path / "fake_project"
    fake_root.mkdir()
    apps_dir = fake_root / "apps"
    apps_dir.mkdir()
    secret_name = "DERIV_" + "API_" + "TOKEN"
    (apps_dir / "leaked.py").write_text(
        f"{secret_name} = '12345678901234567890123456789012'", encoding="utf-8"
    )

    out_dir = tmp_path / "dist"

    with pytest.raises(RuntimeError, match="Security scan failed"):
        compile_executable(
            project_root=fake_root,
            output_dir=out_dir,
            version="1.0.0",
            platform_name="win64",
            skip_pyinstaller=True,
        )

    # Verify staging was wiped clean
    assert not (out_dir / "TradingLab").exists()
