"""Application factory and entrypoint."""

import uvicorn
from fastapi import FastAPI

from switchboard import __version__
from switchboard.api.health import router as health_router
from switchboard.api.middleware import RequestContextMiddleware
from switchboard.core.logging import configure_logging, get_logger
from switchboard.core.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json or not settings.is_local)
    log = get_logger(__name__)

    app = FastAPI(
        title="Switchboard",
        version=__version__,
        description="Multi-tenant LLM inference and routing platform",
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.state.settings = settings

    log.info("app.created", env=settings.env)
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
