"""Utilitário para geração de chaves Ed25519 de assinatura de lease de licença.

Gera:
(a) PEM privado para a variável de ambiente do servidor (LICENSE_SIGNING_KEY_PEM);
(b) Chave pública em formato urlsafe-base64;
(c) Snippet Python para apps/auth_agent/pinned_keys.py.
"""

from __future__ import annotations

import argparse
import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_signing_key(key_id: str) -> tuple[str, str, str]:
    """Gera um par de chaves Ed25519 e retorna (private_pem, pubkey_b64, snippet)."""
    private_key = Ed25519PrivateKey.generate()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pubkey_b64 = base64.urlsafe_b64encode(public_bytes).decode("ascii")

    snippet = (
        "from __future__ import annotations\n\n"
        "# Chaves públicas de produção autorizadas para verificação de lease\n"
        "PINNED_LEASE_KEYS: dict[str, str] = {\n"
        f'    "{key_id}": "{pubkey_b64}",\n'
        "}\n"
    )

    return private_pem, pubkey_b64, snippet


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera chave de assinatura de lease Ed25519 e snippet de pinning."
    )
    parser.add_argument(
        "--key-id",
        required=True,
        help="Identificador da chave (ex: tl-2026-09)",
    )
    args = parser.parse_args()

    key_id = args.key_id.strip()
    if not key_id:
        print("Erro: --key-id não pode ser vazio.", file=sys.stderr)
        return 1

    private_pem, pubkey_b64, snippet = generate_signing_key(key_id)

    print("=" * 70)
    print(f"CHAVE DE ASSINATURA DE PRODUÇÃO (Key ID: {key_id})")
    print("=" * 70)
    print("\n(a) PEM PRIVADO — SOMENTE PARA O SERVIDOR (LICENSE_SIGNING_KEY_PEM):")
    print("-" * 70)
    print(private_pem.strip())
    print("-" * 70)
    print("AVISO DE SEGURANÇA: NUNCA COMMITE ESTE PEM NO REPOSITÓRIO!")
    print("\n(b) PUBKEY URLSAFE-B64:")
    print("-" * 70)
    print(pubkey_b64)
    print("-" * 70)
    print("\n(c) SNIPPET PARA apps/auth_agent/pinned_keys.py:")
    print("-" * 70)
    print(snippet.strip())
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
