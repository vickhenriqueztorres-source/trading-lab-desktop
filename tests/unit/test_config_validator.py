"""Testes unitários para o módulo apps.core.config_validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.core.config_validator import (
    ConfigValidationError,
    ConfigValidator,
    ValidatedConfig,
    parse_env_file,
    validate_config,
)


def test_valid_config_with_defaults() -> None:
    """Valida que configurações corretas com defaults são aprovadas com sucesso."""
    valid_data = {
        "DERIV_API_TOKEN": "valid_token_xyz_sample",
        "IQOPTION_EMAIL": "trader@example.com",
        "IQOPTION_PASSWORD": "secret_password_sample",
    }
    config = validate_config(overrides=valid_data)

    assert config.deriv_api_token == "valid_token_xyz_sample"
    assert config.iqoption_email == "trader@example.com"
    assert config.iqoption_password == "secret_password_sample"
    assert config.trading_mode == "demo"
    assert config.environment == "dev"
    assert config.log_level == "INFO"
    assert config.max_daily_loss is None
    assert config.max_daily_loss_minor_units is None
    assert config.enable_telemetry is True
    assert config.backup_enabled is True
    assert config.ipc_timeout_seconds == 5
    assert config.ipc_max_retries == 3


def test_valid_config_with_all_explicit_values() -> None:
    """Valida que todas as variáveis explícitas são tipadas corretamente."""
    data = {
        "DERIV_API_TOKEN": "deriv_token_abc",
        "IQOPTION_EMAIL": "iq_user@corretora.com",
        "IQOPTION_PASSWORD": "password_sample_value",
        "TRADING_MODE": "simulated",
        "ENVIRONMENT": "staging",
        "LOG_LEVEL": "DEBUG",
        "MAX_DAILY_LOSS": "75.5",
        "ENABLE_TELEMETRY": "false",
        "BACKUP_ENABLED": "0",
        "IPC_TIMEOUT_SECONDS": "10",
        "IPC_MAX_RETRIES": "5",
    }
    config = validate_config(overrides=data)

    assert config.trading_mode == "simulated"
    assert config.environment == "staging"
    assert config.log_level == "DEBUG"
    assert config.max_daily_loss == 75.5
    assert config.max_daily_loss_minor_units == 7550
    assert config.enable_telemetry is False
    assert config.backup_enabled is False
    assert config.ipc_timeout_seconds == 10
    assert config.ipc_max_retries == 5


def test_missing_required_fields_accumulates_all_errors() -> None:
    """Garante que a validação não para no primeiro erro e lista todos os campos ausentes."""
    validator = ConfigValidator()
    empty_data: dict[str, str] = {}

    with pytest.raises(ConfigValidationError) as exc_info:
        validator.validate_dict(empty_data)

    err = exc_info.value
    assert len(err.errors) >= 3
    var_names = [e.variable for e in err.errors]
    assert "DERIV_API_TOKEN" in var_names
    assert "IQOPTION_EMAIL" in var_names
    assert "IQOPTION_PASSWORD" in var_names

    # Testa formatação de texto da exceção
    error_text = str(err)
    assert "DERIV_API_TOKEN" in error_text
    assert "IQOPTION_EMAIL" in error_text
    assert "IQOPTION_PASSWORD" in error_text


def test_placeholder_credentials_rejected() -> None:
    """Garante que placeholders padrão do .env.example são rejeitados."""
    placeholder_data = {
        "DERIV_API_TOKEN": "seu_deriv_api_token_aqui",
        "IQOPTION_EMAIL": "seu_email@dominio.com",
        "IQOPTION_PASSWORD": "sua_senha_secreta_aqui",
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        ConfigValidator().validate_dict(placeholder_data)

    var_names = [e.variable for e in exc_info.value.errors]
    assert "DERIV_API_TOKEN" in var_names
    assert "IQOPTION_EMAIL" in var_names
    assert "IQOPTION_PASSWORD" in var_names


def test_invalid_email_format() -> None:
    """Valida que formatos inválidos de e-mail disparam erro com sugestão clara."""
    data = {
        "DERIV_API_TOKEN": "valid_token",
        "IQOPTION_EMAIL": "email_invalido_sem_arroba",
        "IQOPTION_PASSWORD": "valid_password",
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(overrides=data)

    errs = exc_info.value.errors
    assert any(
        e.variable == "IQOPTION_EMAIL" and "Formato de e-mail inválido" in e.message for e in errs
    )


def test_invalid_enums() -> None:
    """Garante que valores fora dos enums especificados são rejeitados."""
    data = {
        "DERIV_API_TOKEN": "valid_token",
        "IQOPTION_EMAIL": "user@domain.com",
        "IQOPTION_PASSWORD": "valid_password",
        "TRADING_MODE": "invalid_mode",
        "ENVIRONMENT": "invalid_env",
        "LOG_LEVEL": "INVALID_LOG",
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(overrides=data)

    var_names = [e.variable for e in exc_info.value.errors]
    assert "TRADING_MODE" in var_names
    assert "ENVIRONMENT" in var_names
    assert "LOG_LEVEL" in var_names


def test_invalid_numbers_and_negative_limits() -> None:
    """Valida rejeição de números negativos ou não convertíveis."""
    data = {
        "DERIV_API_TOKEN": "valid_token",
        "IQOPTION_EMAIL": "user@domain.com",
        "IQOPTION_PASSWORD": "valid_password",
        "MAX_DAILY_LOSS": "-10.0",
        "IPC_TIMEOUT_SECONDS": "0",
        "IPC_MAX_RETRIES": "-1",
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(overrides=data)

    var_names = [e.variable for e in exc_info.value.errors]
    assert "MAX_DAILY_LOSS" in var_names
    assert "IPC_TIMEOUT_SECONDS" in var_names
    assert "IPC_MAX_RETRIES" in var_names


def test_live_production_requires_max_daily_loss() -> None:
    """Verifica dependência cruzada: live em production exige MAX_DAILY_LOSS."""
    data = {
        "DERIV_API_TOKEN": "live_token_sample",
        "IQOPTION_EMAIL": "user@domain.com",
        "IQOPTION_PASSWORD": "valid_password",
        "TRADING_MODE": "live",
        "ENVIRONMENT": "production",
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(overrides=data)

    errs = exc_info.value.errors
    assert any(
        e.variable == "MAX_DAILY_LOSS" and "Obrigatório quando TRADING_MODE='live'" in e.message
        for e in errs
    )


def test_parse_env_file(tmp_path: Path) -> None:
    """Testa leitura de arquivo .env temporário."""
    env_content = """
    # Comentário
    DERIV_API_TOKEN="token_do_arquivo"
    IQOPTION_EMAIL='arquivo@teste.com'
    IQOPTION_PASSWORD=senha_sem_aspas
    IPC_TIMEOUT_SECONDS=8
    """
    env_file = tmp_path / ".env.test"
    env_file.write_text(env_content, encoding="utf-8")

    parsed = parse_env_file(env_file)
    assert parsed["DERIV_API_TOKEN"] == "token_do_arquivo"
    assert parsed["IQOPTION_EMAIL"] == "arquivo@teste.com"
    assert parsed["IQOPTION_PASSWORD"] == "senha_sem_aspas"
    assert parsed["IPC_TIMEOUT_SECONDS"] == "8"

    # Carrega e valida via arquivo
    config = ConfigValidator().load_and_validate(env_file=env_file)
    assert config.deriv_api_token == "token_do_arquivo"
    assert config.ipc_timeout_seconds == 8


def test_to_dict_redaction() -> None:
    """Valida mascaramento de credenciais na conversão para dicionário."""
    config = ValidatedConfig(
        deriv_api_token="token_sensivel",
        iqoption_email="user@domain.com",
        iqoption_password="senha_super_secreta",
    )
    redacted = config.to_dict(redact_secrets=True)
    assert redacted["DERIV_API_TOKEN"] == "***REDACTED***"
    assert redacted["IQOPTION_PASSWORD"] == "***REDACTED***"
    assert redacted["IQOPTION_EMAIL"] == "user@domain.com"

    plain = config.to_dict(redact_secrets=False)
    assert plain["DERIV_API_TOKEN"] == "token_sensivel"
    assert plain["IQOPTION_PASSWORD"] == "senha_super_secreta"
