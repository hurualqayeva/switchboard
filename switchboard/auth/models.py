from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "developer", "viewer"]


class Tenant(BaseModel):
    id: str
    name: str
    tier: str = "standard"
    created_at: datetime


class ApiKey(BaseModel):
    id: str
    tenant_id: str
    name: str
    key_prefix: str
    role: Role
    rate_limit_rpm: int | None = None
    rate_limit_tpm: int | None = None
    created_at: datetime
    revoked_at: datetime | None = None


class Principal(BaseModel):
    """Who is calling: resolved from the API key on every request."""

    tenant_id: str
    tenant_name: str
    tenant_tier: str
    api_key_id: str
    role: Role
    rate_limit_rpm: int | None = None
    rate_limit_tpm: int | None = None

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles
