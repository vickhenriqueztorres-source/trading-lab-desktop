"""R-VEND-1..2: only the isolated adapter can load the verified vendor."""

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest
from strategy_lab.collect.vendor_integrity import verify_vendor

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/iqoptionapi"


def vendor_imports(source):
    result = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or "", *[alias.name for alias in node.names]]
        elif isinstance(node, ast.Call) and node.args:
            target = node.func
            if (
                isinstance(target, ast.Name)
                and target.id == "__import__"
                or isinstance(target, ast.Attribute)
                and target.attr == "import_module"
            ):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names = [first.value]
        if any("iqoptionapi" in name.split(".") for name in names):
            result.append(node.lineno)
    return result


def test_vendor_import_boundary():
    """R-VEND-2: scan owned Lab source; untouched third-party self-imports are excluded."""
    violations = []
    for directory, subdirs, files in os.walk(ROOT):
        subdirs[:] = [
            name
            for name in subdirs
            if name
            not in {
                "vendor",
                "state",
                "dist",
                ".venv",
                "__pycache__",
                ".git",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                "build",
            }
        ]
        for name in files:
            path = Path(directory) / name
            if path.suffix != ".py":
                continue
            if path.relative_to(ROOT).as_posix() == "tools/strategy_lab/collect/iq_client.py":
                continue
            if vendor_imports(path.read_text(encoding="utf-8")):
                violations.append(path.relative_to(ROOT).as_posix())
    assert not violations


@pytest.mark.parametrize(
    "source",
    [
        "import iqoptionapi",
        "import iqoptionapi.api as vendor",
        "from iqoptionapi.api import X",
        "from vendor import iqoptionapi",
        '__import__("iqoptionapi")',
        'importlib.import_module("iqoptionapi.api")',
    ],
)
def test_boundary_detects_direct_and_dynamic_imports(source):
    """R-VEND-2: AST detector itself has positive controls."""
    assert vendor_imports(source)


def test_snapshot_integrity_and_patch_scope():
    """R-VEND-1: every upstream file present, exactly three authorized patched sources."""
    verify_vendor(VENDOR)
    manifest = json.loads((ROOT / "vendor/iqoptionapi.integrity.json").read_text())
    upstream = manifest["upstream_sha256"]
    patched = manifest["patched_sha256"]
    assert set(upstream) == set(patched)
    assert len(upstream) == 86
    assert {name for name in upstream if upstream[name] != patched[name]} == {
        "__init__.py",
        "api.py",
        "ws/objects/timesync.py",
    }
    assert hashlib.sha256((VENDOR / "LICENSE").read_bytes()).hexdigest() == upstream["LICENSE"]
    assert manifest["commit"] == (VENDOR / "UPSTREAM_COMMIT").read_text().strip()


def test_import_or_alteration_is_detected(tmp_path):
    """R-VEND-1: injected source and changed hash fail before vendor import."""
    snapshot = tmp_path / "iqoptionapi"
    snapshot.mkdir()
    (snapshot / "UPSTREAM_COMMIT").write_text("frozen")
    (snapshot / "x.py").write_text("pass")
    manifest = {"commit": "frozen", "patched_sha256": {"x.py": "0" * 64}}
    (tmp_path / "iqoptionapi.integrity.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="IQ_VENDOR_INTEGRITY_FAILED"):
        verify_vendor(snapshot)
    (snapshot / "extra.py").write_text("pass")
    with pytest.raises(RuntimeError, match="IQ_VENDOR_INTEGRITY_FAILED"):
        verify_vendor(snapshot)
