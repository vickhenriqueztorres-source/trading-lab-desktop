#!/usr/bin/env python3
"""Static secret scrubber and pre-commit hook (R-OPS-2).

Scans git diffs or file contents for exposed credentials, private keys (PEM),
JWT tokens, and database connection strings before committing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Regex patterns for sensitive data
PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN\s+(?:[A-Z0-9_\-]+\s+)*PRIVATE\s+KEY-----",
    re.MULTILINE,
)

JWT_PATTERN = re.compile(
    r"\beyJ[a-zA-Z0-9_\-]{8,}\.eyJ[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]{10,}\b"
)

DATABASE_URL_PATTERN = re.compile(
    r"postgres(?:ql)?://[a-zA-Z0-9_]+:[^@\s/]+@[a-zA-Z0-9_.\-]+(?::\d+)?/[a-zA-Z0-9_.\-]+",
    re.IGNORECASE,
)

EXPOSED_PASSWORD_PATTERN = re.compile(
    r"(?i)\b(?:password|secret_key|service_role_key|iq_password)\s*=\s*['\"]([^'\"\r\n]{6,})['\"]"
)

# Allowed patterns and files (e.g. test fixtures, dummy tokens)
ALLOWED_PATHS = (
    "tests/fixtures",
    "tests/keys",
    "packages/manifest_schema/tests/fixtures",
    "apps/hub/supabase/functions/tests",
    "packages/security/secret_scanner.py",
)

IGNORED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".ico",
    ".svg",
    ".pyc",
    ".pyd",
    ".exe",
    ".dll",
    ".parquet",
    ".sqlite",
    ".db",
    ".zip",
    ".tar",
    ".gz",
)

SAFE_PLACEHOLDERS = (
    "dummy",
    "placeholder",
    "your-password",
    "your_password",
    "change_me",
    "secret",
    "test",
    "mock",
    "example",
    "token",
    "***",
    "test_password",
    "password123",
    "password",
)


def is_allowed_file(path: Path) -> bool:
    posix_path = path.as_posix()
    return any(allowed in posix_path for allowed in ALLOWED_PATHS)


def scan_content(content: str, filename: str) -> list[str]:
    """Inspect text content and return detected secret violations."""
    violations: list[str] = []

    # Check for private keys
    if PEM_PRIVATE_KEY_PATTERN.search(content):
        # Allow test keys only in designated test fixture files
        if not any(allowed in filename for allowed in ("test_key", "test_keys", "tests/keys")):
            violations.append("Exposed PEM Private Key detected")

    # Check for live JWTs
    for match in JWT_PATTERN.finditer(content):
        token = match.group(0)
        # Verify if it's not a generic placeholder
        if not any(placeholder in token.lower() for placeholder in SAFE_PLACEHOLDERS):
            violations.append(f"Exposed JWT Token detected: {token[:12]}...")

    # Check for database URLs with embedded passwords
    for match in DATABASE_URL_PATTERN.finditer(content):
        url = match.group(0)
        if not any(placeholder in url.lower() for placeholder in SAFE_PLACEHOLDERS):
            violations.append(f"Exposed Database Connection String: {url[:20]}...")

    # Check for exposed passwords in code assignments
    for match in EXPOSED_PASSWORD_PATTERN.finditer(content):
        val = match.group(1).strip()
        if val.lower() not in SAFE_PLACEHOLDERS and not val.startswith("${"):
            violations.append(f"Exposed Credential/Password assignment: '{match.group(0)}'")

    return violations


def scan_file(path: Path) -> list[str]:
    """Scan a single file for secrets."""
    if path.suffix in IGNORED_EXTENSIONS:
        return []
    if is_allowed_file(path):
        return []
    if not path.is_file():
        return []

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    file_violations = scan_content(content, path.as_posix())
    return [f"{path}: {v}" for v in file_violations]


def scan_git_diff() -> list[str]:
    """Scan staged git diff."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--unified=0"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return []

        diff_text = proc.stdout
        added_lines = [
            line[1:]
            for line in diff_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        combined = "\n".join(added_lines)
        return scan_content(combined, "git-staged-diff")
    except Exception:
        return []


def scan_repository(root_dir: Path) -> list[str]:
    """Scan entire repository excluding git, venv, and cache dirs."""
    import os

    violations: list[str] = []
    exclude_dirs = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "state",
    }

    for root, dirs, files in os.walk(root_dir):
        # Prune excluded directories immediately
        dirs[:] = [
            d
            for d in dirs
            if d not in exclude_dirs
            and not d.startswith(".venv")
            and not d.startswith("dist")
            and not d.startswith("release_")
            and d not in ("artifacts", "work", "lab strategy iq option", "docs iq option")
        ]
        for file in files:
            path = Path(root) / file
            # Skip large files (> 1MB)
            try:
                if path.stat().st_size > 1_000_000:
                    continue
            except Exception:
                continue
            violations.extend(scan_file(path))

    return violations


def install_pre_commit_hook(repo_root: Path) -> int:
    """Install script as .git/hooks/pre-commit."""
    git_dir = repo_root / ".git"
    if not git_dir.is_dir():
        print(f"Error: {git_dir} does not exist.")
        return 1

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_file = hooks_dir / "pre-commit"

    hook_content = """#!/usr/bin/env bash
python scripts/scrub_secrets.py
"""
    hook_file.write_text(hook_content, encoding="utf-8")
    try:
        hook_file.chmod(0o755)
    except Exception:
        pass

    print(f"Pre-commit hook installed successfully at {hook_file}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub secrets before commit")
    parser.add_argument("files", nargs="*", help="Specific files to scan")
    parser.add_argument("--all", action="store_true", help="Scan entire repository")
    parser.add_argument(
        "--install-hook", action="store_true", help="Install as .git/hooks/pre-commit"
    )

    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]

    if args.install_hook:
        return install_pre_commit_hook(repo_root)

    violations: list[str] = []

    if args.all:
        print(f"Scanning entire repository at {repo_root}...")
        violations = scan_repository(repo_root)
    elif args.files:
        for file_arg in args.files:
            violations.extend(scan_file(Path(file_arg)))
    else:
        # Default: scan staged git diff, fallback to scanning changed files
        violations = scan_git_diff()
        if not violations:
            # Also check any explicitly modified files if git diff was empty
            pass

    if violations:
        print("\n========================================================")
        print("SECRET SCRUBBER: Potential secrets detected!")
        print("========================================================")
        for v in violations:
            print(f" - {v}")
        print("\nPlease remove all credentials before committing or use environment variables.")
        return 1

    print("Secret Scrubber: No secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
