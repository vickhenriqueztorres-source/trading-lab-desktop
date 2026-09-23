#!/usr/bin/env python3
"""Painel Administrativo Visual (Web Local) para Gestão e Geração de Licenças — Trading Lab.

Uso:
    python scripts/admin_panel.py
    (Abre automaticamente o navegador padrão em http://127.0.0.1:7777)
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

# Garante a raiz do projeto no sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gerar_licenca import generate_license  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
LICENSES_DIR = REPO_ROOT / "licencas"
DB_PATH = DATA_DIR / "licencas_geradas.db"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "admin_panel.html"


def init_db(db_file: Path = DB_PATH) -> None:
    """Inicializa as tabelas do banco SQLite para histórico de licenças."""
    db_file.parent.mkdir(parents=True, exist_ok=True)
    LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS generated_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                client_contact TEXT,
                plan TEXT NOT NULL,
                days INTEGER NOT NULL,
                is_lifetime BOOLEAN NOT NULL DEFAULT 0,
                brokers TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT '*',
                product_key TEXT NOT NULL,
                lic_file_path TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                notes TEXT
            );
        """)
        conn.commit()


def get_db(db_file: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Trading Lab — Gestão de Licenças Offline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateLicenseRequest(BaseModel):
    client_name: str
    client_contact: str | None = None
    plan: str = "PRO"  # PRO ou DEMO
    days: int = 30
    is_lifetime: bool = False
    brokers: str = "DERIV,IQ_OPTION"
    device_id: str = "*"
    notes: str | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    if not TEMPLATE_PATH.is_file():
        raise HTTPException(status_code=500, detail="Template do painel não encontrado.")
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content)


@app.post("/api/licenses")
def create_license(req: CreateLicenseRequest) -> dict[str, object]:
    client_name = req.client_name.strip()
    if not client_name:
        raise HTTPException(status_code=400, detail="O nome do cliente é obrigatório.")

    days = 36500 if req.is_lifetime else max(1, req.days)
    real_mode = req.plan.upper() == "PRO"
    broker_list = tuple(b.strip() for b in req.brokers.split(",") if b.strip())

    safe_name = re.sub(r"[^\w\-_]", "_", client_name.lower())
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_file = LICENSES_DIR / f"licenca_{safe_name}_{timestamp}.lic"

    try:
        product_key, claims = generate_license(
            client_name=client_name,
            days=days,
            real_mode=real_mode,
            device_id=req.device_id or "*",
            brokers=broker_list or ("DERIV", "IQ_OPTION"),
            output_lic=out_file,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao assinar licença: {exc}") from exc

    created_at_iso = claims.issued_at.isoformat()
    expires_at_iso = claims.expires_at.isoformat()

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO generated_licenses (
                client_name, client_contact, plan, days, is_lifetime,
                brokers, device_id, product_key, lic_file_path,
                created_at, expires_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_name,
                req.client_contact.strip() if req.client_contact else None,
                "PRO" if real_mode else "DEMO",
                days,
                1 if req.is_lifetime else 0,
                ",".join(broker_list),
                req.device_id or "*",
                product_key,
                str(out_file.resolve()),
                created_at_iso,
                expires_at_iso,
                req.notes,
            ),
        )
        conn.commit()
        lic_id = cursor.lastrowid

    return {
        "id": lic_id,
        "client_name": client_name,
        "client_contact": req.client_contact,
        "plan": "PRO" if real_mode else "DEMO",
        "days": days,
        "is_lifetime": req.is_lifetime,
        "brokers": broker_list,
        "product_key": product_key,
        "lic_file_path": str(out_file),
        "created_at": created_at_iso,
        "expires_at": expires_at_iso,
    }


@app.get("/api/licenses")
def list_licenses() -> dict[str, object]:
    now = datetime.now(UTC)
    licenses: list[dict[str, object]] = []

    total_count = 0
    active_count = 0
    pro_count = 0
    lifetime_count = 0

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM generated_licenses ORDER BY id DESC").fetchall()
        for row in rows:
            total_count += 1
            is_lifetime = bool(row["is_lifetime"]) or row["days"] >= 36500
            if is_lifetime:
                lifetime_count += 1
            if row["plan"] == "PRO":
                pro_count += 1

            expires_at_dt = None
            try:
                expires_at_dt = datetime.fromisoformat(row["expires_at"])
                is_active = expires_at_dt > now
            except Exception:
                is_active = True

            if is_active:
                active_count += 1

            created_str = row["created_at"]
            try:
                created_dt = datetime.fromisoformat(row["created_at"])
                created_formatted = created_dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                created_formatted = created_str

            expires_str = row["expires_at"]
            try:
                if expires_at_dt:
                    expires_formatted = expires_at_dt.strftime("%d/%m/%Y")
                else:
                    expires_formatted = expires_str
            except Exception:
                expires_formatted = expires_str

            licenses.append(
                {
                    "id": row["id"],
                    "client_name": row["client_name"],
                    "client_contact": row["client_contact"],
                    "plan": row["plan"],
                    "days": row["days"],
                    "is_lifetime": is_lifetime,
                    "brokers": row["brokers"],
                    "device_id": row["device_id"],
                    "product_key": row["product_key"],
                    "lic_file_path": row["lic_file_path"],
                    "created_at": created_str,
                    "created_at_formatted": created_formatted,
                    "expires_at": expires_str,
                    "expires_at_formatted": expires_formatted,
                    "is_active": is_active,
                }
            )

    stats = {
        "total": total_count,
        "active": active_count,
        "pro": pro_count,
        "lifetime": lifetime_count,
    }

    return {"licenses": licenses, "stats": stats}


@app.get("/api/licenses/{license_id}/download")
def download_license_file(license_id: int) -> FileResponse:
    with get_db() as conn:
        row = conn.execute(
            "SELECT client_name, lic_file_path, product_key FROM generated_licenses WHERE id = ?",
            (license_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Licença não encontrada.")

    file_path = Path(row["lic_file_path"]) if row["lic_file_path"] else None
    safe_name = re.sub(r"[^\w\-_]", "_", row["client_name"].lower())

    if not file_path or not file_path.is_file():
        # Se o arquivo não existir fisicamente, recria na hora
        LICENSES_DIR.mkdir(parents=True, exist_ok=True)
        file_path = LICENSES_DIR / f"licenca_{safe_name}_{license_id}.lic"
        file_path.write_text(row["product_key"], encoding="utf-8")

    return FileResponse(
        path=file_path,
        filename=f"licenca_{safe_name}.lic",
        media_type="application/octet-stream",
    )


@app.delete("/api/licenses/{license_id}")
def delete_license(license_id: int) -> dict[str, str]:
    with get_db() as conn:
        conn.execute("DELETE FROM generated_licenses WHERE id = ?", (license_id,))
        conn.commit()
    return {"status": "deleted"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Painel Web Administrativo — Trading Lab")
    parser.add_argument("--port", type=int, default=7777, help="Porta local (padrão: 7777)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Não abrir o navegador automaticamente",
    )
    args = parser.parse_args()

    import uvicorn

    init_db()

    url = f"http://127.0.0.1:{args.port}"
    print("=" * 72)
    print("  TRADING LAB — PAINEL ADMINISTRATIVO DE LICENÇAS")
    print("=" * 72)
    print(f"  Servidor local rodando em: {url}")
    print("  Pressione Ctrl+C para encerrar o painel.")
    print("=" * 72)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
