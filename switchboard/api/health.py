"""Liveness and readiness endpoints."""

from fastapi import APIRouter
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
async def readyz() -> HealthResponse:
    """Readiness: dependencies are reachable. Phase 1 adds Postgres and Redis checks."""
    return HealthResponse(status="ready", version=__version__)
