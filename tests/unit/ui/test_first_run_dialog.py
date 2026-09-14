"""Unit tests for FirstRunDialog and onboarding persistence."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from apps.ui.i18n import t
from apps.ui.onboarding.first_run_dialog import (
    FirstRunDialog,
    is_onboarding_done,
    set_onboarding_done,
)


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_onboarding_persistence_roundtrip(tmp_path: Path) -> None:
    profile = tmp_path / "test_profile"
    assert is_onboarding_done(profile) is False
    assert is_onboarding_done(None) is False

    set_onboarding_done(profile)
    assert is_onboarding_done(profile) is True


def test_first_run_dialog_navigation_headless(tmp_path: Path) -> None:
    _ = _get_qapp()
    profile = tmp_path / "test_profile_dialog"
    assert is_onboarding_done(profile) is False

    dialog = FirstRunDialog(profile)
    assert dialog.isModal() is True
    assert dialog.windowTitle() == t("onboarding.title")
    assert dialog._stack.count() == 3
    assert dialog._current_step == 0
    assert dialog._lbl_step1.objectName() == "StepIndicatorActive"
    assert dialog._btn_next.text() == t("onboarding.next")

    # Advance to Step 2
    dialog._on_next()
    assert dialog._current_step == 1
    assert dialog._lbl_step2.objectName() == "StepIndicatorActive"
    assert dialog._btn_next.text() == t("onboarding.next")

    # Advance to Step 3
    dialog._on_next()
    assert dialog._current_step == 2
    assert dialog._lbl_step3.objectName() == "StepIndicatorActive"
    assert dialog._btn_next.text() == t("onboarding.finish")

    # Advance from Step 3 -> finishes and persists
    dialog._on_next()
    assert is_onboarding_done(profile) is True


def test_first_run_dialog_skip_headless(tmp_path: Path) -> None:
    _ = _get_qapp()
    profile = tmp_path / "test_profile_skip"
    dialog = FirstRunDialog(profile)
    assert is_onboarding_done(profile) is False

    dialog._on_skip()
    assert is_onboarding_done(profile) is True


def test_first_run_dialog_retranslate_headless(tmp_path: Path) -> None:
    _ = _get_qapp()
    dialog = FirstRunDialog(tmp_path)
    dialog.retranslate()
    assert dialog.windowTitle() == t("onboarding.title")
    assert dialog._btn_skip.text() == t("onboarding.skip")
