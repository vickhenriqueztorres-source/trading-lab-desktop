"""Security isolation tests: AST and build inspection (R-ISO-2..6)."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_MODULE_PREFIXES = (
    "strategy_lab",
    "strategy-lab",
    "primitives",
    "manifest_schema",
    "polars",
    "duckdb",
    "psycopg",
    "pyarrow",
)


def test_ast_import_scan_prohibits_strategy_lab() -> None:
    """AST/import scan verifies that no Python file in apps/ or packages/ imports Strategy Lab."""
    scanned_count = 0
    violations: list[str] = []

    for directory in (BOT_ROOT / "apps", BOT_ROOT / "packages"):
        for py_file in directory.rglob("*.py"):
            scanned_count += 1
            try:
                tree = ast.parse(py_file.read_bytes(), filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in FORBIDDEN_MODULE_PREFIXES:
                            if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                                violations.append(f"{py_file}:{node.lineno} imports '{alias.name}'")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in FORBIDDEN_MODULE_PREFIXES:
                        if node.module == forbidden or node.module.startswith(f"{forbidden}."):
                            violations.append(
                                f"{py_file}:{node.lineno} imports from '{node.module}'"
                            )

    assert scanned_count > 50, f"Expected > 50 files scanned, got {scanned_count}"
    assert len(violations) == 0, f"Found {len(violations)} isolation violations:\n" + "\n".join(
        violations
    )


def test_build_dependencies_exclude_strategy_lab() -> None:
    """Verify pyproject.toml does not package Strategy Lab dependencies."""
    pyproject_file = BOT_ROOT / "pyproject.toml"
    assert pyproject_file.exists()

    data = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
    dependencies = data.get("project", {}).get("dependencies", [])

    for dep in dependencies:
        dep_name = dep.split("==")[0].split(">=")[0].strip().lower()
        for forbidden in ("polars", "duckdb", "psycopg", "pyarrow", "strategy-lab", "strategy_lab"):
            assert forbidden not in dep_name, (
                f"Forbidden dependency '{forbidden}' found in bot pyproject.toml!"
            )


def test_no_supabase_database_credentials_in_bot() -> None:
    """Verify that no database URLs, service keys, or credentials exist in apps/ or packages/."""
    forbidden_patterns = [
        "postgresql://",
        "postgres://",
        "service_role",
        "SUPABASE_DB_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
    ]
    violations: list[str] = []

    for directory in (BOT_ROOT / "apps", BOT_ROOT / "packages"):
        for py_file in directory.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for pattern in forbidden_patterns:
                if pattern in text:
                    violations.append(f"{py_file} contains forbidden pattern '{pattern}'")

    assert len(violations) == 0, "Found credential/config leaks:\n" + "\n".join(violations)


def test_dist_and_exe_prohibits_strategy_lab() -> None:
    """Verify that build artifacts (EXE and dist/) do not contain Strategy Lab code."""
    dist_dir = BOT_ROOT / "dist" / "TradingLab"
    if not dist_dir.exists():
        return

    violations: list[str] = []
    forbidden_tokens = (b"strategy_lab", b"import primitives", b"from primitives import")

    for path in dist_dir.rglob("*"):
        if path.is_file():
            name_lower = path.name.lower()
            for forb in ("strategy_lab", "polars", "duckdb", "psycopg"):
                if forb in name_lower:
                    violations.append(f"Forbidden build artifact file found: {path}")

            # If it's a Python script or internal metadata file, scan contents
            if path.suffix in (".py", ".pyc", ".json", ".txt", ".toc"):
                content = path.read_bytes()
                for token in forbidden_tokens:
                    if token in content:
                        violations.append(f"File {path.name} contains forbidden token '{token.decode()}'")

    assert len(violations) == 0, "Found Strategy Lab artifacts in dist:\n" + "\n".join(violations)
