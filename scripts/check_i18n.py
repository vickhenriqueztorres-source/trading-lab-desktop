#!/usr/bin/env python3
"""i18n validation script for Trading Lab Desktop.

Validates that:
1. Every key in apps/ui/i18n.py has both 'es' and 'en' translations.
2. No translation strings contain Portuguese-specific words.
3. UI widgets in apps/ui do not use hardcoded user-visible text literals.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from apps.ui.i18n import TRANSLATIONS  # noqa: E402

# Distinctively Portuguese words (excluding valid Spanish cognates like 'saldo', 'entrar')
PORTUGUESE_BLACKLIST = [
    "você",
    "voce",
    "não",
    "nao",
    "configurações",
    "configuracoes",
    "conectado ao",
    "estratégia",
    "operações",
    "operacoes",
    "lucro",
    "perda",
    "carregando",
    "aguarde",
    "sair",
    "conta",
    "senha",
    "usuário",
    "falha",
    "sucesso",
    "atualizar",
]

# Methods / constructors that typically display text to users
TARGET_CALLS = {
    "setText",
    "setToolTip",
    "setPlaceholderText",
    "setWindowTitle",
    "QLabel",
    "QPushButton",
    "addTab",
}

# Symbols/constants/formats that are acceptable as raw literals (not user natural language)
EXEMPT_LITERAL_PATTERNS = [
    r"^[\s\d\W]*$",  # punctuation, spaces, numbers, math symbols, bullets ("●", "—", "·", "...")
    r"^#[0-9a-fA-F]{3,8}$",  # hex colors
    r"^[A-Z0-9_]+$",  # IDs, tokens, uppercase symbols ("DERIV", "IQOPTION", "AUTO", "CALL", "PUT")
    r"^https?://.*",  # URLs
    r"^[a-z0-9_.-]+@[a-z0-9_.-]+",  # emails
    r"^[0-9]+[a-z%sm]?$",  # units or metrics like "5s", "100%", "45ms"
    r"^(\$|USD|EUR)?\s*[0-9.,]+\s*(\$|USD|EUR)?$",  # currencies like "$ 0.00 USD"
    r"^(Deriv|IQ Option|IQOPTION|DERIV|RSI|M1|OTC|Forex|PRO|FREE|Demo|Practice|Real)$",
]


def is_exempt_literal(text: str) -> bool:
    if not text.strip():
        return True
    return any(re.match(pattern, text.strip()) for pattern in EXEMPT_LITERAL_PATTERNS)


def check_translations() -> list[str]:
    errors: list[str] = []
    for key, bundle in TRANSLATIONS.items():
        if "es" not in bundle or not bundle["es"]:
            errors.append(f"Missing 'es' translation for key '{key}'")
        if "en" not in bundle or not bundle["en"]:
            errors.append(f"Missing 'en' translation for key '{key}'")

        for lang in ("es", "en"):
            val = bundle.get(lang, "")
            if not isinstance(val, str):
                continue
            lower_val = val.lower()
            for pt_word in PORTUGUESE_BLACKLIST:
                # check word boundaries or exact substring
                pattern = rf"(?<!\w){re.escape(pt_word)}(?!\w)"
                if re.search(pattern, lower_val):
                    errors.append(
                        f"Portuguese word '{pt_word}' found in key '{key}' [{lang}]: '{val}'"
                    )
    return errors


class LiteralTextVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.errors: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in TARGET_CALLS:
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    val = arg.value
                    if not is_exempt_literal(val):
                        self.errors.append(
                            f"{self.filename}:{node.lineno}: Hardcoded literal in "
                            f"{func_name}(): '{val}' (should use t())"
                        )
        self.generic_visit(node)


def check_ui_code() -> list[str]:
    errors: list[str] = []
    ui_dir = REPO_ROOT / "apps" / "ui"
    for py_path in ui_dir.glob("**/*.py"):
        if "_legacy" in py_path.parts:
            continue
        if py_path.name in ("i18n.py", "tokens.py", "theme.py", "icons.py"):
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        except Exception as e:
            errors.append(f"Failed to parse {py_path}: {e}")
            continue
        visitor = LiteralTextVisitor(str(py_path.relative_to(REPO_ROOT)))
        visitor.visit(tree)
        errors.extend(visitor.errors)
    return errors


def main() -> int:
    print("Checking i18n translations dictionary...")
    t_errors = check_translations()

    print("Checking for unlocalized UI text literals...")
    c_errors = check_ui_code()

    all_errors = t_errors + c_errors
    if all_errors:
        print(f"\nFAILED: Found {len(all_errors)} i18n issue(s):")
        for err in all_errors:
            print(f"  - {err}")
        return 1

    print(
        "SUCCESS: All i18n checks passed! (0 Portuguese words, 100% ES/EN coverage, "
        "zero hardcoded strings)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
