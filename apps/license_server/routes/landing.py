"""Public landing page route for Trading Lab License Server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from apps.license_server.settings import Settings, get_settings

router = APIRouter(tags=["landing"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.get("/", response_class=HTMLResponse)
def get_landing(request: Request) -> Any:
    """Render public Spanish landing page."""
    settings = _settings(request)
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "renew_url": settings.renew_url,
            "download_url": settings.download_url,
            "support_contact_url": settings.support_contact_url,
        },
    )
