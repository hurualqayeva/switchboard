"""Admin endpoints: tenants, API keys, usage summaries. Require the admin role."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from switchboard.api.deps import AdminPrincipal, StorageDep
from switchboard.auth.models import ApiKey, Role, Tenant
from switchboard.ledger.models import UsageSummary
from switchboard.schemas.errors import InvalidRequestError

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tier: str = "standard"


class CreateKeyRequest(BaseModel):
    tenant_id: str
    name: str = Field(min_length=1, max_length=120)
    role: Role = "developer"
    rate_limit_rpm: int | None = Field(default=None, ge=1)
    rate_limit_tpm: int | None = Field(default=None, ge=1)


class CreateKeyResponse(BaseModel):
    key: ApiKey
    secret: str = Field(description="Shown exactly once. Store it now.")


@router.post("/tenants", response_model=Tenant, status_code=201)
async def create_tenant(
    body: CreateTenantRequest, _: AdminPrincipal, storage: StorageDep
) -> Tenant:
    try:
        return await storage.auth.create_tenant(body.name, body.tier)
    except Exception as exc:  # unique violation from either store
        raise InvalidRequestError(f"could not create tenant: {exc}", param="name") from exc


@router.get("/tenants", response_model=list[Tenant])
async def list_tenants(_: AdminPrincipal, storage: StorageDep) -> list[Tenant]:
    return await storage.auth.list_tenants()


@router.post("/keys", response_model=CreateKeyResponse, status_code=201)
async def create_key(
    body: CreateKeyRequest, _: AdminPrincipal, storage: StorageDep
) -> CreateKeyResponse:
    if await storage.auth.get_tenant(body.tenant_id) is None:
        raise InvalidRequestError(f"unknown tenant '{body.tenant_id}'", param="tenant_id")
    key, secret = await storage.auth.create_key(
        body.tenant_id,
        body.name,
        body.role,
        rate_limit_rpm=body.rate_limit_rpm,
        rate_limit_tpm=body.rate_limit_tpm,
    )
    return CreateKeyResponse(key=key, secret=secret)


@router.get("/tenants/{tenant_id}/keys", response_model=list[ApiKey])
async def list_keys(tenant_id: str, _: AdminPrincipal, storage: StorageDep) -> list[ApiKey]:
    return await storage.auth.list_keys(tenant_id)


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, _: AdminPrincipal, storage: StorageDep) -> None:
    if not await storage.auth.revoke_key(key_id):
        raise InvalidRequestError(f"key '{key_id}' not found or already revoked", param="key_id")


@router.get("/usage", response_model=UsageSummary)
async def usage(
    _: AdminPrincipal,
    storage: StorageDep,
    tenant_id: str | None = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> UsageSummary:
    until = until or datetime.now(UTC)
    since = since or (until - timedelta(days=30))
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return await storage.usage.summary(tenant_id=tenant_id, since=since, until=until)
