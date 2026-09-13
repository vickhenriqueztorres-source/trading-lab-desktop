"""Módulo de validação de configurações e variáveis de ambiente do Trading Lab Desktop.

Valida todas as variáveis de ambiente e configurações antes do bootstrap do sistema,
garantindo integridade de tipos, limites numéricos, formatos e regras de negócio.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
VALID_TRADING_MODES = frozenset({"live", "demo", "simulated"})
VALID_ENVIRONMENTS = frozenset({"dev", "staging", "production"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
PLACEHOLDER_PATTERNS = frozenset(
    {
        "seu_deriv_api_token_aqui",
        "seu_email@dominio.com",
        "sua_senha_secreta_aqui",
        "your_token_here",
        "your_email_here",
    }
)


@dataclass(frozen=True, slots=True)
class ConfigErrorItem:
    """Representa um erro ou inconsistência em uma variável de configuração.

    Attributes:
        variable: Nome da variável de ambiente com erro.
        message: Descrição clara do erro encontrado.
        suggestion: Sugestão de correção para o operador.
    """

    variable: str
    message: str
    suggestion: str

    def __str__(self) -> str:
        return f"- {self.variable}: {self.message} (Sugestão: {self.suggestion})"


class ConfigValidationError(Exception):
    """Exceção levantada quando uma ou mais configurações falham na validação.

    Contém a lista completa de todos os erros encontrados para que o operador
    possa corrigir tudo de uma só vez.
    """

    def __init__(self, errors: list[ConfigErrorItem]) -> None:
        self.errors = list(errors)
        formatted_errors = "\n".join(str(err) for err in self.errors)
        super().__init__(
            f"Configuração inválida ({len(self.errors)} erro(s) detectado(s)):\n{formatted_errors}"
        )


@dataclass(frozen=True, slots=True)
class ValidatedConfig:
    """Configurações validadas e tipadas prontas para consumo pelo sistema."""

    deriv_api_token: str
    iqoption_email: str
    iqoption_password: str
    trading_mode: str = "demo"
    environment: str = "dev"
    log_level: str = "INFO"
    max_daily_loss: float | None = None
    enable_telemetry: bool = True
    backup_enabled: bool = True
    ipc_timeout_seconds: int = 5
    ipc_max_retries: int = 3

    @property
    def max_daily_loss_minor_units(self) -> int | None:
        """Converte a perda máxima diária em minor units inteiros (centavos)."""
        if self.max_daily_loss is None:
            return None
        return int(round(self.max_daily_loss * 100))

    def to_dict(self, redact_secrets: bool = True) -> dict[str, Any]:
        """Converte para dicionário com opção de mascarar credenciais confidenciais."""
        token_display = "***REDACTED***" if redact_secrets else self.deriv_api_token
        password_display = "***REDACTED***" if redact_secrets else self.iqoption_password
        return {
            "DERIV_API_TOKEN": token_display,
            "IQOPTION_EMAIL": self.iqoption_email,
            "IQOPTION_PASSWORD": password_display,
            "TRADING_MODE": self.trading_mode,
            "ENVIRONMENT": self.environment,
            "LOG_LEVEL": self.log_level,
            "MAX_DAILY_LOSS": self.max_daily_loss,
            "ENABLE_TELEMETRY": self.enable_telemetry,
            "BACKUP_ENABLED": self.backup_enabled,
            "IPC_TIMEOUT_SECONDS": self.ipc_timeout_seconds,
            "IPC_MAX_RETRIES": self.ipc_max_retries,
        }


def parse_env_file(filepath: Path | str) -> dict[str, str]:
    """Carrega variáveis de um arquivo .env sem dependências externas.

    Args:
        filepath: Caminho para o arquivo .env.

    Returns:
        Dicionário com as chaves e valores lidos.
    """
    path = Path(filepath)
    if not path.is_file():
        return {}

    env_data: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                logger.warning("Linha ignorada no .env (sem '='): %s:%d", path, line_num)
                continue

            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()

            # Remove aspas envolventes simples ou duplas
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]

            env_data[key] = val

    return env_data


def _parse_bool(value: Any, default: bool = True) -> bool:
    """Converte valores variados para booleano."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    str_val = str(value).strip().lower()
    if str_val in {"true", "1", "yes", "sim", "y", "on"}:
        return True
    if str_val in {"false", "0", "no", "nao", "não", "n", "off"}:
        return False
    raise ValueError(f"Valor booleano inválido: '{value}'")


class ConfigValidator:
    """Validador de configurações do Trading Lab Desktop."""

    def __init__(self, schema_path: Path | str | None = None) -> None:
        """Inicializa o validador com schema opcional.

        Args:
            schema_path: Caminho opcional para o config_schema.json.
        """
        self._schema: dict[str, Any] | None = None
        target_path = (
            Path(schema_path) if schema_path else Path(__file__).parent / "config_schema.json"
        )
        if target_path.is_file():
            try:
                with open(target_path, encoding="utf-8") as f:
                    self._schema = json.load(f)
            except Exception as exc:
                logger.warning("Não foi possível carregar config_schema.json: %s", exc)

    def load_and_validate(
        self,
        env_file: Path | str | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> ValidatedConfig:
        """Carrega do arquivo .env ou do ambiente os parâmetros e os valida.

        Args:
            env_file: Caminho opcional para o arquivo .env.
            overrides: Dicionário de sobrescrita para testes ou injeção direta.

        Returns:
            Instância de ValidatedConfig com dados válidos.

        Raises:
            ConfigValidationError: Se houver um ou mais erros nas configurações.
        """
        raw_env: dict[str, Any] = dict(os.environ)

        # 1. Carrega do .env se existir
        if env_file:
            loaded = parse_env_file(env_file)
            raw_env.update(loaded)
        else:
            default_env_path = Path(".env")
            if default_env_path.is_file():
                raw_env.update(parse_env_file(default_env_path))

        # 2. Aplica overrides se informados
        if overrides:
            raw_env.update(overrides)

        return self.validate_dict(raw_env)

    def validate_dict(self, config_dict: Mapping[str, Any]) -> ValidatedConfig:
        """Valida um mapeamento de configurações diretamente.

        Args:
            config_dict: Dicionário contendo as chaves de configuração.

        Returns:
            ValidatedConfig com os dados normalizados e tipados.

        Raises:
            ConfigValidationError: Se qualquer validação falhar.
        """
        errors: list[ConfigErrorItem] = []

        # 1. DERIV_API_TOKEN
        deriv_token = str(config_dict.get("DERIV_API_TOKEN", "")).strip()
        if not deriv_token:
            errors.append(
                ConfigErrorItem(
                    variable="DERIV_API_TOKEN",
                    message="Variável obrigatória está ausente ou vazia.",
                    suggestion="Defina DERIV_API_TOKEN com seu token gerado no painel da Deriv.",
                )
            )
        elif deriv_token.lower() in PLACEHOLDER_PATTERNS:
            errors.append(
                ConfigErrorItem(
                    variable="DERIV_API_TOKEN",
                    message="Valor detectado é um placeholder padrão.",
                    suggestion="Substitua o token de exemplo pelo seu token real da Deriv.",
                )
            )

        # 2. IQOPTION_EMAIL
        iq_email = str(config_dict.get("IQOPTION_EMAIL", "")).strip()
        if not iq_email:
            errors.append(
                ConfigErrorItem(
                    variable="IQOPTION_EMAIL",
                    message="Variável obrigatória está ausente ou vazia.",
                    suggestion="Defina IQOPTION_EMAIL com o e-mail da sua conta IQ Option.",
                )
            )
        elif not EMAIL_REGEX.match(iq_email):
            errors.append(
                ConfigErrorItem(
                    variable="IQOPTION_EMAIL",
                    message=f"Formato de e-mail inválido: '{iq_email}'.",
                    suggestion="Forneça um e-mail válido no formato usuario@dominio.com.",
                )
            )
        elif iq_email.lower() in PLACEHOLDER_PATTERNS:
            errors.append(
                ConfigErrorItem(
                    variable="IQOPTION_EMAIL",
                    message="Valor detectado é um e-mail de exemplo.",
                    suggestion="Substitua pelo e-mail cadastrado na conta IQ Option.",
                )
            )

        # 3. IQOPTION_PASSWORD
        iq_pass = str(config_dict.get("IQOPTION_PASSWORD", "")).strip()
        if not iq_pass:
            errors.append(
                ConfigErrorItem(
                    variable="IQOPTION_PASSWORD",
                    message="Variável obrigatória está ausente ou vazia.",
                    suggestion="Defina IQOPTION_PASSWORD com a senha da sua conta IQ Option.",
                )
            )
        elif iq_pass.lower() in PLACEHOLDER_PATTERNS:
            errors.append(
                ConfigErrorItem(
                    variable="IQOPTION_PASSWORD",
                    message="Valor detectado é uma senha de exemplo.",
                    suggestion="Substitua pela sua senha real da IQ Option.",
                )
            )

        # 4. TRADING_MODE
        trading_mode = str(config_dict.get("TRADING_MODE", "demo")).strip().lower()
        if trading_mode not in VALID_TRADING_MODES:
            errors.append(
                ConfigErrorItem(
                    variable="TRADING_MODE",
                    message=(
                        f"Modo inválido: '{trading_mode}'. "
                        f"Esperado um de: {sorted(VALID_TRADING_MODES)}."
                    ),
                    suggestion="Configure TRADING_MODE como 'demo', 'live' ou 'simulated'.",
                )
            )

        # 5. ENVIRONMENT
        environment = str(config_dict.get("ENVIRONMENT", "dev")).strip().lower()
        if environment not in VALID_ENVIRONMENTS:
            errors.append(
                ConfigErrorItem(
                    variable="ENVIRONMENT",
                    message=(
                        f"Ambiente inválido: '{environment}'. "
                        f"Esperado um de: {sorted(VALID_ENVIRONMENTS)}."
                    ),
                    suggestion="Configure ENVIRONMENT como 'dev', 'staging' ou 'production'.",
                )
            )

        # 6. LOG_LEVEL
        log_level = str(config_dict.get("LOG_LEVEL", "INFO")).strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            errors.append(
                ConfigErrorItem(
                    variable="LOG_LEVEL",
                    message=(
                        f"Nível de log inválido: '{log_level}'. "
                        f"Esperado um de: {sorted(VALID_LOG_LEVELS)}."
                    ),
                    suggestion="Configure LOG_LEVEL como 'DEBUG', 'INFO', 'WARNING' ou 'ERROR'.",
                )
            )

        # 7. MAX_DAILY_LOSS
        max_daily_loss: float | None = None
        raw_loss = config_dict.get("MAX_DAILY_LOSS")
        if raw_loss is not None and str(raw_loss).strip() != "":
            try:
                parsed_loss = float(raw_loss)
                if parsed_loss <= 0:
                    errors.append(
                        ConfigErrorItem(
                            variable="MAX_DAILY_LOSS",
                            message=f"Deve ser um número maior que zero. Recebido: {parsed_loss}.",
                            suggestion="Defina MAX_DAILY_LOSS com um número positivo (ex: 50.0).",
                        )
                    )
                else:
                    max_daily_loss = parsed_loss
            except (ValueError, TypeError):
                errors.append(
                    ConfigErrorItem(
                        variable="MAX_DAILY_LOSS",
                        message=f"Não foi possível converter '{raw_loss}' para número decimal.",
                        suggestion="Defina MAX_DAILY_LOSS com um valor numérico válido (ex: 50.0).",
                    )
                )

        # 8. ENABLE_TELEMETRY
        enable_telemetry = True
        try:
            enable_telemetry = _parse_bool(config_dict.get("ENABLE_TELEMETRY", True), default=True)
        except ValueError as exc:
            errors.append(
                ConfigErrorItem(
                    variable="ENABLE_TELEMETRY",
                    message=str(exc),
                    suggestion="Defina ENABLE_TELEMETRY como 'true' ou 'false'.",
                )
            )

        # 9. BACKUP_ENABLED
        backup_enabled = True
        try:
            backup_enabled = _parse_bool(config_dict.get("BACKUP_ENABLED", True), default=True)
        except ValueError as exc:
            errors.append(
                ConfigErrorItem(
                    variable="BACKUP_ENABLED",
                    message=str(exc),
                    suggestion="Defina BACKUP_ENABLED como 'true' ou 'false'.",
                )
            )

        # 10. IPC_TIMEOUT_SECONDS
        ipc_timeout = 5
        raw_timeout = config_dict.get("IPC_TIMEOUT_SECONDS", 5)
        try:
            ipc_timeout = int(raw_timeout)
            if ipc_timeout <= 0:
                errors.append(
                    ConfigErrorItem(
                        variable="IPC_TIMEOUT_SECONDS",
                        message=f"Deve ser um número inteiro > 0. Recebido: {ipc_timeout}.",
                        suggestion="Defina IPC_TIMEOUT_SECONDS com um valor como 5 ou 10.",
                    )
                )
        except (ValueError, TypeError):
            errors.append(
                ConfigErrorItem(
                    variable="IPC_TIMEOUT_SECONDS",
                    message=f"Não foi possível converter '{raw_timeout}' para inteiro.",
                    suggestion="Forneça um número inteiro positivo para IPC_TIMEOUT_SECONDS.",
                )
            )

        # 11. IPC_MAX_RETRIES
        ipc_retries = 3
        raw_retries = config_dict.get("IPC_MAX_RETRIES", 3)
        try:
            ipc_retries = int(raw_retries)
            if ipc_retries < 0:
                errors.append(
                    ConfigErrorItem(
                        variable="IPC_MAX_RETRIES",
                        message=f"Deve ser um número inteiro >= 0. Recebido: {ipc_retries}.",
                        suggestion="Defina IPC_MAX_RETRIES com 0 ou mais retentativas.",
                    )
                )
        except (ValueError, TypeError):
            errors.append(
                ConfigErrorItem(
                    variable="IPC_MAX_RETRIES",
                    message=f"Não foi possível converter '{raw_retries}' para inteiro.",
                    suggestion="Forneça um número inteiro não negativo para IPC_MAX_RETRIES.",
                )
            )

        # 12. Validação cruzada de dependências
        # Em modo 'live' no ambiente 'production', MAX_DAILY_LOSS é obrigatório
        if trading_mode == "live" and environment == "production" and max_daily_loss is None:
            errors.append(
                ConfigErrorItem(
                    variable="MAX_DAILY_LOSS",
                    message="Obrigatório quando TRADING_MODE='live' e ENVIRONMENT='production'.",
                    suggestion="Defina um teto diário em MAX_DAILY_LOSS para operar em produção.",
                )
            )

        # Se houver qualquer erro, loga e levanta ConfigValidationError
        if errors:
            logger.error("Falha na validação de configurações: %d erro(s) encontrados", len(errors))
            for err in errors:
                logger.error("  %s", err)
            raise ConfigValidationError(errors)

        return ValidatedConfig(
            deriv_api_token=deriv_token,
            iqoption_email=iq_email,
            iqoption_password=iq_pass,
            trading_mode=trading_mode,
            environment=environment,
            log_level=log_level,
            max_daily_loss=max_daily_loss,
            enable_telemetry=enable_telemetry,
            backup_enabled=backup_enabled,
            ipc_timeout_seconds=ipc_timeout,
            ipc_max_retries=ipc_retries,
        )


def validate_config(
    env_file: Path | str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> ValidatedConfig:
    """Função utilitária canônica para validar e obter as configurações do sistema."""
    return ConfigValidator().load_and_validate(env_file=env_file, overrides=overrides)
