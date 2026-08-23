from __future__ import annotations

import pytest

from apps.ui.i18n import TRANSLATIONS, I18nManager, t


def test_i18n_complete_key_parity_between_languages() -> None:
    for key, lang_dict in TRANSLATIONS.items():
        assert "en" in lang_dict, f"Missing English translation for {key}"
        assert "es" in lang_dict, f"Missing Spanish translation for {key}"
        assert len(lang_dict["en"].strip()) > 0
        assert len(lang_dict["es"].strip()) > 0


def test_i18n_language_switching() -> None:
    I18nManager.set_language("es")
    assert I18nManager.get_language() == "es"
    assert t("app.practice_badge") == "MODO PRÁCTICA"
    assert t("btn.safe_stop") == "DETENER NUEVAS ENTRADAS (SAFE STOP)"

    I18nManager.set_language("en")
    assert I18nManager.get_language() == "en"
    assert t("app.practice_badge") == "PRACTICE MODE"
    assert t("btn.safe_stop") == "STOP NEW ENTRIES (SAFE STOP)"

    # Restore default
    I18nManager.set_language("es")


def test_i18n_parameter_formatting() -> None:
    I18nManager.set_language("en")
    msg = t("diag.message", path="test.zip", size=1024, sha256="abc")
    assert "test.zip" in msg
    assert "1024" in msg
    assert "abc" in msg


def test_i18n_subscribe_listener() -> None:
    I18nManager.set_language("es")
    events: list[str] = []

    def listener(lang: str) -> None:
        events.append(lang)

    I18nManager.subscribe(listener)
    try:
        I18nManager.set_language("en")
        I18nManager.set_language("es")
        assert events == ["en", "es"]
    finally:
        I18nManager.unsubscribe(listener)


def test_i18n_unknown_key_returns_key() -> None:
    assert t("non_existent_key_12345") == "non_existent_key_12345"


def test_i18n_unsupported_language_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported language"):
        I18nManager.set_language("fr")
