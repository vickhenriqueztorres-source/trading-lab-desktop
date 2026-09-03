"""R-COL-1/I-8/I-14: secrets come only from the independent Lab namespace."""

import uuid
from types import SimpleNamespace

import pytest
from strategy_lab.collect import credentials


def test_keyring_uses_dedicated_service_not_environment(monkeypatch):
    """R-COL-1: default path uses OS keyring and never falls back to app env."""
    value = uuid.uuid4().hex
    monkeypatch.delenv("STRATEGY_LAB_ENV", raising=False)
    monkeypatch.setenv("IQ_EMAIL", "wrong")
    monkeypatch.setenv("IQ_PASSWORD", "wrong")
    fake_keyring = SimpleNamespace(
        get_credential=lambda service, username: SimpleNamespace(username=value, password=value)
    )
    monkeypatch.setattr(credentials, "_load_keyring_module", lambda: fake_keyring)
    result = credentials.load_credentials()
    assert result.username.get_secret_value() == value
    assert value not in repr(result)
    assert credentials.WINDOWS_TARGET == "StrategyLab/IQOption/collection"
    assert credentials.KEYRING_SERVICE == credentials.WINDOWS_TARGET


def test_vps_environment_is_namespaced_and_never_logged(monkeypatch):
    """R-COL-1: env fallback exists only for the explicit VPS mode."""
    value = uuid.uuid4().hex
    monkeypatch.setenv("STRATEGY_LAB_ENV", "vps")
    monkeypatch.setenv("IQ_EMAIL", value)
    monkeypatch.setenv("IQ_PASSWORD", value)
    result = credentials.load_credentials()
    assert result.username.get_secret_value() == value
    assert value not in str(result)


def test_missing_vps_credentials_fail_closed(monkeypatch):
    """R-COL-1: missing VPS secret never causes fallback to samples or another profile."""
    monkeypatch.setenv("STRATEGY_LAB_ENV", "vps")
    monkeypatch.delenv("IQ_EMAIL", raising=False)
    monkeypatch.delenv("IQ_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="IQ_COLLECTION_CREDENTIALS_UNAVAILABLE"):
        credentials.load_credentials()


def test_environment_is_ignored_outside_vps(monkeypatch):
    """R-COL-1: env fallback is disabled unless Strategy Lab runs in VPS mode."""
    monkeypatch.delenv("STRATEGY_LAB_ENV", raising=False)
    monkeypatch.setenv("IQ_EMAIL", uuid.uuid4().hex)
    monkeypatch.setenv("IQ_PASSWORD", uuid.uuid4().hex)
    fake_keyring = SimpleNamespace(get_credential=lambda service, username: None)
    monkeypatch.setattr(credentials, "_load_keyring_module", lambda: fake_keyring)
    with pytest.raises(RuntimeError, match="IQ_COLLECTION_CREDENTIALS_UNAVAILABLE"):
        credentials.load_credentials()
