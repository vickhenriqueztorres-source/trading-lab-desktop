from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from re import Pattern

DEFAULT_SCAN_EXTENSIONS = (".py", ".json", ".md")
DEFAULT_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "venv",
    }
)
MAX_SCAN_FILE_BYTES = 2 * 1024 * 1024
MAX_SCAN_FILES = 10_000
MAX_SCAN_MATCHES = 1_000


class SecretKind(StrEnum):
    PRIVATE_KEY = "PRIVATE_KEY"
    JWT = "JWT"
    BEARER_TOKEN = "BEARER_TOKEN"
    DERIV_TOKEN = "DERIV_TOKEN"
    OTP = "OTP"
    SESSION_COOKIE = "SESSION_COOKIE"
    PASSWORD = "PASSWORD"
    AUTHORIZATION_HEADER = "AUTHORIZATION_HEADER"


class SecretScanError(RuntimeError):
    reason_code = "SECRET_SCAN_FAILED"


@dataclass(frozen=True, slots=True)
class SecretMatch:
    kind: SecretKind
    line_number: int
    column_number: int
    matched_length: int
    fingerprint: str
    source_path: str | None = None

    def __post_init__(self) -> None:
        if self.line_number <= 0 or self.column_number <= 0 or self.matched_length <= 0:
            raise ValueError("secret match location must be positive")
        if len(self.fingerprint) != 12:
            raise ValueError("secret match fingerprint must be a redacted SHA-256 prefix")


@dataclass(frozen=True, slots=True)
class ScanReport:
    scanned_files: int
    skipped_files: int
    total_matches: int
    matches: tuple[SecretMatch, ...]

    @property
    def is_clean(self) -> bool:
        return self.total_matches == 0


@dataclass(frozen=True, slots=True)
class _SecretRule:
    kind: SecretKind
    pattern: Pattern[str]


class SecretScanner:
    """Bounded local scanner that reports only location and redacted fingerprints."""

    _RULES = (
        _SecretRule(
            SecretKind.PRIVATE_KEY,
            re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE),
        ),
        _SecretRule(
            SecretKind.AUTHORIZATION_HEADER,
            re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
        ),
        _SecretRule(
            SecretKind.BEARER_TOKEN,
            re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
        ),
        _SecretRule(
            SecretKind.JWT,
            re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        ),
        _SecretRule(
            SecretKind.DERIV_TOKEN,
            re.compile(
                r"(?:deriv[_ -]?token|api[_ -]?token)\s*\\?[\"']?\s*[:=]\s*"
                r"\\?[\"']?(?=[A-Za-z0-9_-]{8,})(?=[A-Za-z0-9_-]*\d)"
                r"[A-Za-z0-9_-]+",
                re.IGNORECASE,
            ),
        ),
        _SecretRule(
            SecretKind.OTP,
            re.compile(
                r"(?:otp|one[_ -]?time[_ -]?(?:code|password)|verification[_ -]?code|"
                r"challenge[_ -]?code)\s*[\"']?\s*[:=]\s*[\"']?\d{6}\b",
                re.IGNORECASE,
            ),
        ),
        _SecretRule(
            SecretKind.SESSION_COOKIE,
            re.compile(
                r"(?:(?:Cookie|Set-Cookie)\s*:\s*[A-Za-z0-9_.-]{1,64}=|"
                r"session[_ -]?cookie\s*\\?[\"']?\s*[:=]\s*\\?[\"']?)"
                r"(?=[^\s\"']{8,})(?=[^\s\"']*\d)[^\s\"']+",
                re.IGNORECASE,
            ),
        ),
        _SecretRule(
            SecretKind.PASSWORD,
            re.compile(
                r"(?:password|passwd|pwd)\s*\\?[\"']?\s*[:=]\s*\\?[\"']?"
                r"(?=[^\s\"']{8,})(?=[^\s\"']*\d)[^\s\"']+",
                re.IGNORECASE,
            ),
        ),
    )

    def __init__(
        self,
        *,
        max_file_bytes: int = MAX_SCAN_FILE_BYTES,
        max_files: int = MAX_SCAN_FILES,
        max_matches: int = MAX_SCAN_MATCHES,
    ) -> None:
        if not 1 <= max_file_bytes <= MAX_SCAN_FILE_BYTES:
            raise ValueError("secret scanner file byte limit is outside the bounded range")
        if not 1 <= max_files <= MAX_SCAN_FILES:
            raise ValueError("secret scanner file count is outside the bounded range")
        if not 1 <= max_matches <= MAX_SCAN_MATCHES:
            raise ValueError("secret scanner match count is outside the bounded range")
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files
        self._max_matches = max_matches

    def scan_text(self, text: str) -> list[SecretMatch]:
        if len(text.encode("utf-8")) > self._max_file_bytes:
            raise SecretScanError(SecretScanError.reason_code)
        return self._scan_text(text, source_path=None)

    def scan_file(self, file_path: Path) -> list[SecretMatch]:
        try:
            if file_path.is_symlink() or not file_path.is_file():
                raise OSError("secret scan target must be a regular file")
            with file_path.open("rb") as handle:
                encoded = handle.read(self._max_file_bytes + 1)
            if len(encoded) > self._max_file_bytes:
                raise OSError("secret scan file exceeds byte limit")
            text = encoded.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SecretScanError(SecretScanError.reason_code) from exc
        return self._scan_text(text, source_path=str(file_path))

    def scan_directory(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS,
    ) -> ScanReport:
        normalized_extensions = self._validate_extensions(extensions)
        try:
            if target_dir.is_symlink() or not target_dir.is_dir():
                raise OSError("secret scan target must be a regular directory")
            candidates: list[Path] = []
            for path in target_dir.rglob("*"):
                if path.suffix.lower() not in normalized_extensions or any(
                    part in DEFAULT_EXCLUDED_DIRECTORIES for part in path.parts
                ):
                    continue
                candidates.append(path)
                if len(candidates) > self._max_files:
                    raise OSError("secret scan directory exceeds file limit")
            candidates.sort(key=lambda path: path.as_posix())
            matches: list[SecretMatch] = []
            skipped_files = 0
            for candidate in candidates:
                if not candidate.is_file():
                    skipped_files += 1
                    continue
                matches.extend(self.scan_file(candidate))
                if len(matches) > self._max_matches:
                    raise OSError("secret scan directory exceeds match limit")
            return ScanReport(
                scanned_files=len(candidates) - skipped_files,
                skipped_files=skipped_files,
                total_matches=len(matches),
                matches=tuple(matches),
            )
        except SecretScanError:
            raise
        except OSError as exc:
            raise SecretScanError(SecretScanError.reason_code) from exc

    def _scan_text(self, text: str, *, source_path: str | None) -> list[SecretMatch]:
        matches: list[SecretMatch] = []
        for rule in self._RULES:
            for found in rule.pattern.finditer(text):
                line_start = text.rfind("\n", 0, found.start()) + 1
                raw = found.group(0)
                line_number = text.count("\n", 0, found.start()) + 1
                column_number = found.start() - line_start + 1
                fingerprint_source = f"{rule.kind.value}:{line_number}:{column_number}:{len(raw)}"
                matches.append(
                    SecretMatch(
                        kind=rule.kind,
                        line_number=line_number,
                        column_number=column_number,
                        matched_length=len(raw),
                        fingerprint=hashlib.sha256(fingerprint_source.encode("ascii")).hexdigest()[
                            :12
                        ],
                        source_path=source_path,
                    )
                )
                if len(matches) > self._max_matches:
                    raise SecretScanError(SecretScanError.reason_code)
        matches.sort(key=lambda item: (item.line_number, item.column_number, item.kind.value))
        return matches

    @staticmethod
    def _validate_extensions(extensions: tuple[str, ...]) -> frozenset[str]:
        if not extensions or len(extensions) > 16:
            raise ValueError("secret scanner extensions must contain 1 to 16 values")
        normalized = frozenset(extension.lower() for extension in extensions)
        if any(not extension.startswith(".") or len(extension) > 16 for extension in normalized):
            raise ValueError("secret scanner extension is invalid")
        return normalized
