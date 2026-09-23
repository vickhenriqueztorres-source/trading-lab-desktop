import sys
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication

from apps.ui.auth.logic import (
    format_code,
    format_countdown,
    map_auth_error_key,
    mask_email,
    validate_email,
)
from apps.ui.auth.login_window import LoginWindow
from apps.ui.pages.account_page import AccountPage
from packages.protocol import UiAuthStatusResponse


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_mask_email() -> None:
    assert mask_email("julio.cesar@gmail.com") == "ju***@gmail.com"
    assert mask_email("admin@domain.com") == "ad***@domain.com"
    assert mask_email("ab@domain.com") == "a***@domain.com"
    assert mask_email("a@b.com") == "a***@b.com"
    assert mask_email("") == "***"
    assert mask_email(None) == "***"
    assert mask_email("plainstring") == "pl***"


def test_validate_email() -> None:
    assert validate_email("user@example.com") is True
    assert validate_email("first.last+tag@sub.domain.co") is True
    assert validate_email("user.123@domain.org") is True

    assert validate_email("bad-email") is False
    assert validate_email("@domain.com") is False
    assert validate_email("user@") is False
    assert validate_email("user@.com") is False
    assert validate_email("") is False
    assert validate_email(None) is False
    assert validate_email("a" * 260 + "@domain.com") is False


def test_format_code() -> None:
    assert format_code(["1", "2", "3", "4", "5", "6"]) == "123456"
    assert format_code(["a", "B", "c", "D", "e", "F"]) == "aBcDeF"
    assert format_code(["1", " ", "2", "", "3", "4"]) == "1234"
    assert format_code([]) == ""


def test_map_auth_error_key() -> None:
    assert map_auth_error_key("AUTH_OTP_INVALID") == "login.err_otp_invalid"
    assert map_auth_error_key("AUTH_CHALLENGE_EXPIRED") == "login.err_expired"
    assert map_auth_error_key("AUTH_LICENSE_EXPIRED") == "login.err_license_expired"
    assert map_auth_error_key("AUTH_DEVICE_LIMIT") == "login.err_device_limit"
    assert map_auth_error_key("AUTH_SERVICE_UNAVAILABLE") == "login.err_unavailable"
    assert map_auth_error_key("AUTH_RATE_LIMITED") == "login.err_rate_limited"
    assert map_auth_error_key("UNKNOWN_ERROR") == "login.err_unavailable"
    assert map_auth_error_key(None) == "login.err_unavailable"


def test_format_countdown() -> None:
    assert format_countdown(300) == "05:00"
    assert format_countdown(65) == "01:05"
    assert format_countdown(59) == "00:59"
    assert format_countdown(5) == "00:05"
    assert format_countdown(0) == "00:00"
    assert format_countdown(-10) == "00:00"


def test_login_window_instantiation_headless() -> None:
    _ = _get_qapp()
    mock_controller = MagicMock()
    mock_controller.auth_status.return_value = UiAuthStatusResponse(
        authorized=False,
        status="AUTH_REQUIRED",
        user_id_preview=None,
        plan=None,
        expires_at=None,
        device_id=None,
        reason=None,
    )

    window = LoginWindow(mock_controller)
    assert window.isModal() is True
    assert window._stack.count() == 3
    assert window._stack.currentIndex() == LoginWindow._STATE_EMAIL
    assert window._txt_email is not None
    assert len(window._digit_boxes) == 6


def test_login_window_with_initial_error_headless() -> None:
    _ = _get_qapp()
    mock_controller = MagicMock()

    window = LoginWindow(mock_controller, initial_error="AUTH_LICENSE_EXPIRED")
    assert window._lbl_email_error.isVisible() or window._lbl_email_error.text() != ""


def test_account_page_instantiation_and_update_headless() -> None:
    _ = _get_qapp()
    page = AccountPage()

    # Initial state
    assert page._lbl_title.text() != ""

    # Update with authorized status
    status = UiAuthStatusResponse(
        authorized=True,
        status="AUTHORIZED",
        user_id_preview="ju***@gmail.com",
        plan="PRO",
        expires_at="2026-10-15T00:00:00Z",
        device_id="device-abcdef123456",
        reason=None,
    )
    page.update_auth_status(status)
    assert page._lbl_email_val.text() == "ju***@gmail.com"
    assert "PRO" in page._lbl_plan_badge.text().upper()

    # Update with None / unauthenticated
    page.update_auth_status(None)
    assert page._lbl_email_val.text() != ""
    assert page._progress_sub.value() == 0


def test_login_window_server_unavailable_shows_friendly_error() -> None:
    _ = _get_qapp()
    mock_controller = MagicMock()
    window = LoginWindow(mock_controller)
    window._txt_email.setText("trader@domain.com")

    # Simulate network failure / server down exception
    window._on_start_login_finished(ConnectionError("Failed to reach license server"))

    assert not window._lbl_email_error.isHidden()
    from apps.ui.i18n import t

    assert window._lbl_email_error.text() == t("login.err_unavailable")
    assert window._btn_send_code.isEnabled()
