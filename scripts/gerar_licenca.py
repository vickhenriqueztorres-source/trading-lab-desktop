#!/usr/bin/env python3
"""Gerador Administrativo de Chaves de Licença Criptográficas Offline para Trading Lab.

Uso:
    python scripts/gerar_licenca.py --cliente "Nome do Cliente" --dias 30
    python scripts/gerar_licenca.py --cliente "Cliente VIP" --vitalicio --modo-real
    python scripts/gerar_licenca.py --cliente "Teste Demo" --dias 7 --modo-practice
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Garante que a raiz do repositório está no sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from packages.licensing.lease import LeaseSigner  # noqa: E402
from packages.licensing.models import LeaseClaims  # noqa: E402
from packages.licensing.product_key import encode_product_key  # noqa: E402

DEFAULT_KEY_ID = "tl-master-offline"
DEFAULT_PEM_FILE = REPO_ROOT / "master_key.pem"


def load_or_create_master_key(pem_path: Path, key_id: str = DEFAULT_KEY_ID) -> Ed25519PrivateKey:
    """Carrega master_key.pem ou gera um novo par de chaves caso não exista."""
    if pem_path.is_file():
        data = pem_path.read_bytes()
        try:
            private_key = serialization.load_pem_private_key(data, password=None)
            if isinstance(private_key, Ed25519PrivateKey):
                return private_key
        except Exception as exc:
            msg = f"Aviso: Não foi possível ler {pem_path} ({exc}). Gerando nova chave..."
            print(msg, file=sys.stderr)

    # Gera nova chave privada Ed25519
    private_key = Ed25519PrivateKey.generate()
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_path.write_bytes(pem_bytes)

    # Calcula chave pública urlsafe b64
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode("ascii")

    # Atualiza apps/auth_agent/pinned_keys.py se necessário
    pinned_path = REPO_ROOT / "apps" / "auth_agent" / "pinned_keys.py"
    if pinned_path.is_file():
        content = pinned_path.read_text(encoding="utf-8")
        if key_id not in content:
            updated = content.replace(
                "PINNED_LEASE_KEYS: dict[str, str] = {",
                f'PINNED_LEASE_KEYS: dict[str, str] = {{\n    "{key_id}": "{pub_b64}",',
            )
            pinned_path.write_text(updated, encoding="utf-8")
            print(f"-> Chave pública adicionada a {pinned_path.name} (Key ID: {key_id})")

    print(f"-> Nova chave mestra gerada e salva em: {pem_path}")
    return private_key


def generate_license(
    *,
    client_name: str,
    days: int = 365,
    real_mode: bool = True,
    device_id: str = "*",
    brokers: tuple[str, ...] = ("DERIV", "IQ_OPTION"),
    key_id: str = DEFAULT_KEY_ID,
    pem_path: Path = DEFAULT_PEM_FILE,
    output_lic: Path | None = None,
) -> tuple[str, LeaseClaims]:
    """Gera e assina digitalmente uma chave de licença offline."""
    master_key = load_or_create_master_key(pem_path, key_id=key_id)
    signer = LeaseSigner(key_id, master_key)

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=days)

    claims = LeaseClaims(
        format_version=1,
        lease_id=str(uuid4()),
        user_id=client_name.strip() or "Cliente Trading Lab",
        device_id=device_id.strip() or "*",
        issued_at=now,
        expires_at=expires_at,
        plan="PRO" if real_mode else "PHASE0_PRACTICE",
        broker_access=brokers,
        strategy_packs=(
            "core",
            "strategy-test",
            "tail-probability-edge",
            "selective-differs-edge",
            "parity-regime-edge",
            "iqoption-radar",
        ),
        real_mode_allowed=real_mode,
        client_version_min="0.0.1",
        client_version_max="999.99.99",
        nonce=uuid4().hex[:16],
    )

    signed = signer.sign(claims)
    product_key = encode_product_key(signed, prefix="TLKEY-PRO-" if real_mode else "TLKEY-DEMO-")

    if output_lic is not None:
        output_lic.parent.mkdir(parents=True, exist_ok=True)
        output_lic.write_text(product_key, encoding="utf-8")

    return product_key, claims


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gerador Administrativo de Chaves de Licença Offline (Ed25519) — Trading Lab",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cliente",
        default="Cliente Trading Lab",
        help="Nome ou identificador do cliente (ex: 'João Silva', 'cliente@email.com')",
    )
    parser.add_argument(
        "--dias",
        type=int,
        default=365,
        help="Validade da licença em dias (padrão: 365 dias)",
    )
    parser.add_argument(
        "--vitalicio",
        action="store_true",
        help="Licença vitalícia (100 anos / 36500 dias)",
    )
    parser.add_argument(
        "--modo-practice",
        action="store_true",
        help="Limita a licença apenas a contas Demo/Practice (desativa modo Real)",
    )
    parser.add_argument(
        "--dispositivo",
        default="*",
        help="ID específico do dispositivo (padrão: '*' funciona em qualquer máquina)",
    )
    parser.add_argument(
        "--brokers",
        default="DERIV,IQ_OPTION",
        help="Corretoras autorizadas separadas por vírgula (padrão: 'DERIV,IQ_OPTION')",
    )
    parser.add_argument(
        "--saida-lic",
        default=None,
        help="Caminho opcional para salvar o arquivo de licença .lic",
    )

    args = parser.parse_args()

    days = 36500 if args.vitalicio else args.dias
    real_mode = not args.modo_practice
    broker_list = tuple(b.strip() for b in args.brokers.split(",") if b.strip())

    out_lic = Path(args.saida_lic) if args.saida_lic else None

    product_key, claims = generate_license(
        client_name=args.cliente,
        days=days,
        real_mode=real_mode,
        device_id=args.dispositivo,
        brokers=broker_list,
        output_lic=out_lic,
    )

    if args.vitalicio:
        validade_str = "VITALÍCIA (100 anos)"
    else:
        validade_str = f"{days} dias (até {claims.expires_at.strftime('%d/%m/%Y')})"
    tipo_str = "PRO (Real + Demo)" if real_mode else "DEMO (Apenas Contas Practice)"

    disp_info = "Qualquer máquina (*)" if claims.device_id == "*" else claims.device_id

    print("")
    print("=" * 72)
    print("  TRADING LAB — CHAVE DE LICENÇA OFFLINE GERADA COM SUCESSO")
    print("=" * 72)
    print(f"  Cliente:        {claims.user_id}")
    print(f"  Tipo / Plano:   {tipo_str}")
    print(f"  Validade:       {validade_str}")
    print(f"  Corretoras:     {', '.join(claims.broker_access)}")
    print(f"  Dispositivo:    {disp_info}")
    print("-" * 72)
    print("  CHAVE DE ATIVAÇÃO (Copie a linha inteira abaixo):")
    print("-" * 72)
    print(f"\n{product_key}\n")
    print("-" * 72)

    if out_lic:
        print(f"  Arquivo salvo:  {out_lic.resolve()}")
    else:
        # Salva automaticamente uma cópia de conveniência em licenses/
        safe_name = re.sub(r"[^\w\-_]", "_", args.cliente.lower())
        auto_file = REPO_ROOT / f"licenca_{safe_name}.lic"
        auto_file.write_text(product_key, encoding="utf-8")
        print(f"  Arquivo salvo:  {auto_file.name}")

    print("=" * 72)
    print("  Instruções para o cliente:")
    print("  1. Abra o Trading Lab Desktop.")
    print("  2. Cole a chave acima no campo 'Chave de Licença' e clique em 'Ativar'.")
    print("  Pronto! O robô funcionará sem requisições a servidor e sem internet.")
    print("=" * 72)
    print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
