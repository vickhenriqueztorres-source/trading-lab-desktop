"""Configurações padrão embutidas de build para licenciamento e suporte.

Em builds de produção, o desktop sai de fábrica configurado para se conectar
ao servidor de licenças oficial. Variáveis de ambiente correspondentes
têm prioridade máxima para permitir testes em ambientes de staging e homologação.
"""

from __future__ import annotations

import os

# Servidor oficial de autenticação e licenças (produção)
AUTH_BASE_URL_DEFAULT = "https://licencias.tradinglab.app"

# Links oficiais de suporte e renovação
SUPPORT_RENEW_URL_DEFAULT = "https://tradinglab.app/renew"
SUPPORT_CONTACT_URL_DEFAULT = "https://t.me/tradinglab_support"

# Por padrão, simulação de autenticação é SEMPRE desabilitada em produção
FORCE_SIMULATION_DEFAULT = False


def get_auth_base_url() -> str:
    """Retorna a URL base do serviço de identidade e licenciamento.

    A variável de ambiente TRADING_LAB_AUTH_BASE_URL tem prioridade sobre o default.
    """
    env_val = os.environ.get("TRADING_LAB_AUTH_BASE_URL", "").strip()
    return env_val if env_val else AUTH_BASE_URL_DEFAULT


def get_support_renew_url() -> str:
    """Retorna a URL oficial de renovação da licença.

    A variável de ambiente TRADING_LAB_RENEW_URL tem prioridade sobre o default.
    """
    env_val = os.environ.get("TRADING_LAB_RENEW_URL", "").strip()
    return env_val if env_val else SUPPORT_RENEW_URL_DEFAULT


def get_support_contact_url() -> str:
    """Retorna o canal oficial de atendimento ao cliente / suporte.

    A variável de ambiente TRADING_LAB_SUPPORT_URL tem prioridade sobre o default.
    """
    env_val = os.environ.get("TRADING_LAB_SUPPORT_URL", "").strip()
    return env_val if env_val else SUPPORT_CONTACT_URL_DEFAULT
