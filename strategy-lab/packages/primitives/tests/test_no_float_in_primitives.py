"""R-PRIM-3: the primitive implementation contains no float literals or float() calls."""

from __future__ import annotations

import ast
from pathlib import Path


def test_no_float_in_primitives() -> None:
    package = Path(__file__).parents[1] / "primitives"
    violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                violations.append(f"{path}:{node.lineno}:float literal")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "float"
            ):
                violations.append(f"{path}:{node.lineno}:float call")
    assert violations == []
