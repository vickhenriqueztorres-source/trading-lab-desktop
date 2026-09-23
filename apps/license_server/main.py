"""Main entrypoint and FastAPI application factory for Trading Lab License Server."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.license_server.db import close_pool, init_pool, run_migrations, with_conn
from apps.license_server.errors import ApiError, api_error_handler, unhandled_exception_handler
from apps.license_server.keys import init_keys_in_db, load_signing_key
from apps.license_server.middleware import SecurityHeadersMiddleware
from apps.license_server.routes.admin import router as admin_router
from apps.license_server.routes.auth import router as auth_router
from apps.license_server.routes.device import router as device_router
from apps.license_server.routes.health import router as health_router
from apps.license_server.routes.landing import router as landing_router
from apps.license_server.routes.lease import router as lease_router
from apps.license_server.routes.wellknown import router as wellknown_router
from apps.license_server.settings import Settings, get_settings

logger = logging.getLogger("license_server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager initializing settings, keys, and database pool."""
    settings = getattr(app.state, "settings", None) or get_settings()
    app.state.settings = settings

    # 1. Initialize signing keys in memory
    load_signing_key(settings)

    # 2. If database is configured, open pool, migrate, and sync keys
    if settings.database_url:
        try:
            init_pool(settings.database_url)
            with with_conn() as conn:
                run_migrations(conn)
                init_keys_in_db(conn, settings)
        except Exception as exc:
            logger.error("Database startup failed: %s", exc)
            if settings.environment in {"production", "prod"}:
                raise

    yield

    # Shutdown
    close_pool()


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI application factory."""
    active_settings = settings or get_settings()

    app = FastAPI(
        title="Trading Lab License Server",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = active_settings

    # Register middlewares
    app.add_middleware(SecurityHeadersMiddleware)

    # Register standardized error handlers
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Register routers
    app.include_router(landing_router)
    app.include_router(health_router)
    app.include_router(wellknown_router)
    app.include_router(auth_router)
    app.include_router(device_router)
    app.include_router(lease_router)
    app.include_router(admin_router)

    return app


app = create_app()
