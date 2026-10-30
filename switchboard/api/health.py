"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from switchboard import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness: the process is up."""
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=HealthResponse)
async def readyz(request: Request) -> JSONResponse:
    """Readiness: storage is reachable."""
    storage = getattr(request.app.state, "storage", None)
    ready = storage is not None and await storage.ping()
    body = HealthResponse(status="ready" if ready else "not_ready", version=__version__)
    return JSONResponse(status_code=200 if ready else 503, content=body.model_dump())
