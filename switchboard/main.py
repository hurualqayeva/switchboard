"""Application factory and entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from switchboard import __version__
from switchboard.api.admin import router as admin_router
from switchboard.api.errors import install_error_handlers
from switchboard.api.health import router as health_router
from switchboard.api.middleware import RequestContextMiddleware
from switchboard.api.v1 import router as v1_router
from switchboard.catalog.models import load_catalog
from switchboard.core.logging import configure_logging, get_logger
from switchboard.core.settings import Settings, get_settings
from switchboard.executor.executor import Executor
from switchboard.ledger.writer import LedgerWriter
from switchboard.providers.base import ProviderRegistry, build_default_registry
from switchboard.storage.base import Storage
from switchboard.storage.memory import MemoryStorage
from switchboard.storage.postgres import PostgresStorage

log = get_logger(__name__)


def build_storage(settings: Settings) -> Storage:
    if settings.storage == "memory":
        return MemoryStorage()
    return PostgresStorage(str(settings.database_url), auto_migrate=settings.auto_migrate)


async def _seed_dev_key(storage: Storage) -> None:
    """Memory storage starts empty; give local runs an admin key so the API is usable."""
    tenant = await storage.auth.create_tenant("dev", "internal")
    _, secret = await storage.auth.create_key(tenant.id, "dev-admin", "admin")
    log.warning("dev.admin_key", tenant=tenant.id, secret=secret, note="memory storage only")


def create_app(
    settings: Settings | None = None,
    *,
    storage: Storage | None = None,
    registry: ProviderRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json or not settings.is_local)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.catalog = load_catalog(settings.catalog_path)
        app.state.storage = storage or build_storage(settings)
        app.state.registry = registry or build_default_registry()
        await app.state.storage.start()
        if storage is None and settings.storage == "memory" and settings.env == "local":
            await _seed_dev_key(app.state.storage)
        app.state.ledger = LedgerWriter(
            app.state.storage.ledger, flush_interval_s=settings.ledger_flush_interval_s
        )
        await app.state.ledger.start()
        app.state.executor = Executor(app.state.catalog, app.state.registry, app.state.ledger)
        log.info(
            "app.started",
            env=settings.env,
            storage=settings.storage,
            deployments=len(app.state.catalog),
        )
        try:
            yield
        finally:
            await app.state.ledger.stop()
            await app.state.registry.aclose()
            await app.state.storage.close()
            log.info("app.stopped", ledger_rows=app.state.ledger.written)

    app = FastAPI(
        title="Switchboard",
        version=__version__,
        description="Multi-tenant LLM inference and routing platform",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(admin_router)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "switchboard.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.is_local,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
