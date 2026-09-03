"""Verify the frozen third-party source before import (R-VEND-1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def verify_vendor(root: Path) -> None:
    manifest_path = root.parent / "iqoptionapi.integrity.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["patched_sha256"]
    if (root / "UPSTREAM_COMMIT").read_text(encoding="utf-8").strip() != manifest["commit"]:
        raise RuntimeError("IQ_VENDOR_INTEGRITY_FAILED")
    actual_paths = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.name not in {"UPSTREAM_COMMIT", "PATCHES.md"}
    }
    if actual_paths != set(expected):
        raise RuntimeError("IQ_VENDOR_INTEGRITY_FAILED")
    for relative, digest in expected.items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("IQ_VENDOR_INTEGRITY_FAILED")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("IQ_VENDOR_INTEGRITY_FAILED")
