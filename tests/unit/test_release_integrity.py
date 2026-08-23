from __future__ import annotations

import json
from pathlib import Path

from packages.security.integrity import (
    IntegrityIssueType,
    ReleaseIntegrityVerifier,
    ReleaseManifestBuilder,
)


def _setup_sample_package(root: Path) -> tuple[Path, Path]:
    pkg_dir = root / "sample_app"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    (pkg_dir / "main.py").write_text("print('hello')", encoding="utf-8")
    (pkg_dir / "config.json").write_text('{"key": "value"}', encoding="utf-8")

    sub = pkg_dir / "lib"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "helper.py").write_text("def help(): pass", encoding="utf-8")

    manifest = ReleaseManifestBuilder.build_manifest(
        root_dir=pkg_dir,
        version="1.2.3",
        platform="windows_x86_64",
    )
    manifest_path = pkg_dir / "release_manifest.json"
    ReleaseManifestBuilder.write_manifest(manifest, manifest_path)
    return pkg_dir, manifest_path


def test_manifest_builder_and_verifier_roundtrip(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _setup_sample_package(tmp_path)

    result = ReleaseIntegrityVerifier.verify_distribution(pkg_dir, manifest_path)

    assert result.is_valid is True
    assert len(result.issues) == 0


def test_verifier_detects_hash_mismatch(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _setup_sample_package(tmp_path)

    # Tamper with 1 byte in main.py
    main_file = pkg_dir / "main.py"
    main_file.write_text("print('hallo')", encoding="utf-8")

    result = ReleaseIntegrityVerifier.verify_distribution(pkg_dir, manifest_path)

    assert result.is_valid is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.issue_type == IntegrityIssueType.HASH_MISMATCH
    assert issue.relative_path == "main.py"


def test_verifier_detects_missing_file(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _setup_sample_package(tmp_path)

    # Delete config.json
    (pkg_dir / "config.json").unlink()

    result = ReleaseIntegrityVerifier.verify_distribution(pkg_dir, manifest_path)

    assert result.is_valid is False
    assert len(result.issues) == 1
    assert result.issues[0].issue_type == IntegrityIssueType.MISSING_FILE
    assert result.issues[0].relative_path == "config.json"


def test_verifier_detects_untracked_file(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _setup_sample_package(tmp_path)

    # Inject unauthorized file
    (pkg_dir / "backdoor.py").write_text("import os; os.system('calc')", encoding="utf-8")

    result = ReleaseIntegrityVerifier.verify_distribution(pkg_dir, manifest_path)

    assert result.is_valid is False
    assert len(result.issues) == 1
    assert result.issues[0].issue_type == IntegrityIssueType.UNTRACKED_FILE
    assert result.issues[0].relative_path == "backdoor.py"


def test_verifier_detects_corrupted_manifest(tmp_path: Path) -> None:
    pkg_dir, manifest_path = _setup_sample_package(tmp_path)

    # Corrupt manifest content
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["manifest_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    result = ReleaseIntegrityVerifier.verify_distribution(pkg_dir, manifest_path)

    assert result.is_valid is False
    assert any(i.issue_type == IntegrityIssueType.MANIFEST_CORRUPTED for i in result.issues)


def test_manifest_builder_excludes_dev_and_sensitive_artifacts(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "app"
    pkg_dir.mkdir()
    (pkg_dir / "app.py").write_text("x = 1", encoding="utf-8")
    (pkg_dir / "state.db").write_text("sqlite", encoding="utf-8")
    (pkg_dir / "keys.vault").write_text("vault", encoding="utf-8")
    (pkg_dir / ".env").write_text("TOKEN=123", encoding="utf-8")

    pycache = pkg_dir / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-313.pyc").write_text("bytecode", encoding="utf-8")

    manifest = ReleaseManifestBuilder.build_manifest(pkg_dir, "1.0.0")

    assert set(manifest.files.keys()) == {"app.py"}
