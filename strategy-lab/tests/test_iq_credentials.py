"""R-COL-1/I-8/I-14: secrets come only from the independent Lab namespace."""

import json
import uuid
import warnings
from types import SimpleNamespace

import pytest
from strategy_lab import cli
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


def test_interactive_store_uses_dedicated_keyring_and_masks_secret(monkeypatch, capsys):
    """R-COL-1/I-8: setup never receives a password as a CLI argument or prints it."""
    username = f"collector-{uuid.uuid4().hex}@invalid.test"
    opaque_secret = uuid.uuid4().hex
    stored: dict[tuple[str, str], str] = {}
    fake_keyring = SimpleNamespace(
        set_password=lambda service, user, secret: stored.__setitem__((service, user), secret),
        get_password=lambda service, user: stored.get((service, user)),
        get_credential=lambda service, user: (
            SimpleNamespace(username=username, password=stored[(service, username)])
            if (service, username) in stored
            else None
        ),
    )
    monkeypatch.setattr(credentials, "_load_keyring_module", lambda: fake_keyring)

    credentials.prompt_and_store_credentials(
        username_reader=lambda prompt: username,
        password_reader=lambda prompt: opaque_secret,
    )

    assert stored[(credentials.KEYRING_SERVICE, username)] == opaque_secret
    assert credentials.keyring_credentials_available() is True
    output = capsys.readouterr().out
    assert username not in output
    assert opaque_secret not in output


def test_interactive_store_rejects_empty_values_without_writing(monkeypatch):
    """R-COL-1/I-7: incomplete collection credentials fail closed."""
    called = False

    def load_keyring():
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr(credentials, "_load_keyring_module", load_keyring)
    with pytest.raises(RuntimeError, match="IQ_COLLECTION_CREDENTIALS_INVALID"):
        credentials.store_keyring_credentials("", "secret")
    assert called is False


def test_credentials_cli_status_discloses_only_boolean(monkeypatch, capsys):
    """R-COL-1/I-8: status exposes availability without identity or secret."""
    monkeypatch.setattr(cli, "keyring_credentials_available", lambda: True)
    assert cli.main(["credentials", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "configured": True,
        "event": "strategy_lab_collection_credentials_status",
    }


def test_credentials_cli_set_has_no_secret_argument_or_output(monkeypatch, capsys):
    """R-COL-1/I-8: CLI setup invokes only the interactive provider."""
    invoked = False

    def interactive_setup() -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(cli, "prompt_and_store_credentials", interactive_setup)
    assert cli.main(["credentials", "set"]) == 0
    assert invoked is True
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "event": "strategy_lab_collection_credentials_configured",
        "status": "ok",
    }


def test_secure_prompt_refuses_noninteractive_stdin(monkeypatch):
    """R-COL-1/I-8: piped credentials must not trigger an echoed fallback."""
    monkeypatch.setattr(credentials.sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="IQ_COLLECTION_INTERACTIVE_TERMINAL_REQUIRED"):
        credentials.prompt_and_store_credentials(
            username_reader=lambda prompt: pytest.fail("No input may be requested"),
        )


def test_getpass_echo_warning_aborts_without_store(monkeypatch):
    """R-COL-1/I-8: inability to hide password aborts before reading or storing it."""

    def insecure_reader(prompt):
        warnings.warn("fallback", credentials.getpass.GetPassWarning, stacklevel=2)
        pytest.fail("Echoed fallback must never execute")

    monkeypatch.setattr(
        credentials, "store_keyring_credentials", lambda *args: pytest.fail("Must not store")
    )
    with pytest.raises(RuntimeError, match="IQ_COLLECTION_SECURE_INPUT_UNAVAILABLE"):
        credentials.prompt_and_store_credentials(
            username_reader=lambda prompt: "synthetic",
            password_reader=insecure_reader,
        )


def test_keyring_failure_content_is_not_exposed(monkeypatch, capsys):
    """R-COL-1/I-8: a failing native backend may include secrets in its exception."""
    secret = uuid.uuid4().hex

    def fail(*args):
        raise RuntimeError(secret)

    fake = SimpleNamespace(set_password=fail, get_credential=fail)
    monkeypatch.setattr(credentials, "_load_keyring_module", lambda: fake)
    with pytest.raises(RuntimeError, match="^IQ_COLLECTION_CREDENTIALS_STORE_FAILED$"):
        credentials.store_keyring_credentials("synthetic", secret)
    assert credentials.keyring_credentials_available() is False
    assert secret not in str(capsys.readouterr())
