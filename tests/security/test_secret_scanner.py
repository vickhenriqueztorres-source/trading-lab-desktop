from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.security import SecretKind, SecretScanError, SecretScanner

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _synthetic_secret_text() -> tuple[str, tuple[str, ...]]:
    bearer_value = "synthetic" + "-bearer-value-123456"
    jwt_value = "ey" + "Jheader12345.payload12345.signature12345"
    broker_value = "deriv" + "_token=" + "syntheticBrokerToken123"
    otp_value = "".join(("1", "3", "5", "7", "9", "0"))
    cookie_value = "synthetic" + "-session-cookie-value-42"
    password_value = "synthetic" + "-password-value-42"
    private_key_marker = "-----BEGIN" + " PRIVATE KEY-----"
    lines = (
        private_key_marker,
        "Authorization: " + "Bearer " + bearer_value,
        jwt_value,
        broker_value,
        "otp=" + otp_value,
        "Cookie: session=" + cookie_value,
        '"password": "' + password_value + '"',
    )
    raw_values = (
        bearer_value,
        jwt_value,
        otp_value,
        cookie_value,
        password_value,
    )
    return "\n".join(lines), raw_values


def test_secret_scanner_detects_synthetic_secret_families_without_returning_values() -> None:
    text, raw_values = _synthetic_secret_text()

    matches = SecretScanner().scan_text(text)
    kinds = {match.kind for match in matches}

    assert {
        SecretKind.PRIVATE_KEY,
        SecretKind.AUTHORIZATION_HEADER,
        SecretKind.BEARER_TOKEN,
        SecretKind.JWT,
        SecretKind.DERIV_TOKEN,
        SecretKind.OTP,
        SecretKind.SESSION_COOKIE,
        SecretKind.PASSWORD,
    } <= kinds
    redacted_result = repr(matches)
    for raw_value in raw_values:
        assert raw_value not in redacted_result


def test_secret_scanner_scans_files_and_directories_with_extension_filter(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("mode = 'DECISION_ONLY'\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    unsafe = nested / "diagnostic.json"
    text, _ = _synthetic_secret_text()
    unsafe.write_text(json.dumps({"diagnostic": text}), encoding="utf-8")
    ignored = nested / "ignored.txt"
    ignored.write_text(text, encoding="utf-8")

    report = SecretScanner().scan_directory(tmp_path, extensions=(".py", ".json"))

    assert report.scanned_files == 2
    assert report.total_matches >= 8
    assert report.is_clean is False
    assert all(match.source_path is not None for match in report.matches)


def test_secret_scanner_fails_closed_for_oversized_or_unsafe_targets(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_text("x" * 33, encoding="utf-8")
    scanner = SecretScanner(max_file_bytes=32)

    with pytest.raises(SecretScanError, match="SECRET_SCAN_FAILED"):
        scanner.scan_file(oversized)
    with pytest.raises(SecretScanError, match="SECRET_SCAN_FAILED"):
        scanner.scan_text("x" * 33)
    with pytest.raises(SecretScanError, match="SECRET_SCAN_FAILED"):
        scanner.scan_directory(tmp_path / "missing")


def test_repository_code_fixtures_and_documents_are_free_of_detectable_secret_values() -> None:
    report = SecretScanner().scan_directory(PROJECT_ROOT)

    assert report.is_clean, tuple(
        (match.kind.value, match.source_path, match.line_number) for match in report.matches
    )
