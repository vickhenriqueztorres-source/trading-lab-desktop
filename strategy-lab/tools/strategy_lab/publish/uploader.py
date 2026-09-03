"""Upload signed manifest to Supabase Edge Function publish."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UploadResponse:
    status_code: int
    sha256: str | None
    error: str | None
    message: str


class PublishUploadError(Exception):
    """Raised when publish upload fails."""

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(f"Upload failed ({status_code} - {error_code}): {message}")
        self.status_code = status_code
        self.error_code = error_code


def upload_manifest(
    manifest_bytes: bytes,
    endpoint_url: str | None = None,
    auth_token: str | None = None,
    timeout_s: float = 15.0,
) -> UploadResponse:
    """POST signed manifest to Supabase Edge Function publish.

    Handles 201/401/409/422 with clear messages.
    """
    url = endpoint_url or os.environ.get("SUPABASE_PUBLISH_URL")
    if not url:
        supabase_url = os.environ.get("SUPABASE_URL", "https://staging.dualtrade.com")
        url = f"{supabase_url.rstrip('/')}/functions/v1/publish"

    token = (
        auth_token or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("PUBLISH_TOKEN")
    )

    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        url,
        data=manifest_bytes,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            status = response.status
            body_text = response.read().decode("utf-8")
            data: dict[str, Any] = json.loads(body_text) if body_text else {}

            if status == 201:
                sha = data.get("sha256")
                return UploadResponse(
                    status_code=201,
                    sha256=sha,
                    error=None,
                    message=f"Manifesto publicado com sucesso! SHA-256: {sha}",
                )
            return UploadResponse(
                status_code=status,
                sha256=data.get("sha256"),
                error=data.get("error"),
                message=f"Resposta inesperada do hub ({status})",
            )
    except urllib.error.HTTPError as err:
        status = err.code
        err_body = err.read().decode("utf-8", errors="ignore")
        try:
            err_data = json.loads(err_body)
            error_code = str(err_data.get("error", "UNKNOWN_ERROR"))
        except Exception:
            error_code = "HTTP_ERROR"

        if status == 401:
            msg = f"Rejeitado: Assinatura inválida ou chave não autorizada ({error_code})."
        elif status == 409:
            msg = f"Conflito: Versão do manifesto não é estritamente mais recente ({error_code})."
        elif status == 422:
            msg = f"Inválido: Manifesto rejeitado pela validação de schema ({error_code})."
        elif status == 400:
            msg = f"JSON Inválido: Corpo da requisição corrompido ({error_code})."
        else:
            msg = f"Erro no servidor remoto ({status}): {error_code}"

        raise PublishUploadError(status, error_code, msg) from err
    except urllib.error.URLError as err:
        raise PublishUploadError(
            0, "NETWORK_ERROR", f"Falha de conexão com o hub: {err.reason}"
        ) from err
