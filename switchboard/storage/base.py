"""Storage interfaces.

Two implementations exist: Postgres for real deployments and an in-memory one for unit
tests and zero-dependency local demos. Handlers and the executor only see these
protocols, which keeps the request path testable without a database.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from switchboard.auth.models import ApiKey, Principal, Role, Tenant
from switchboard.ledger.models import UsageEvent, UsageSummary


class AuthStore(Protocol):
    async def create_tenant(self, name: str, tier: str = "standard") -> Tenant: ...

    async def get_tenant(self, tenant_id: str) -> Tenant | None: ...

    async def list_tenants(self) -> list[Tenant]: ...

    async def create_key(
        self,
        tenant_id: str,
        name: str,
        role: Role,
        *,
        rate_limit_rpm: int | None = None,
        rate_limit_tpm: int | None = None,
    ) -> tuple[ApiKey, str]:
        """Return the key record and the secret. The secret is never stored or shown again."""
        ...

    async def list_keys(self, tenant_id: str) -> list[ApiKey]: ...

    async def revoke_key(self, key_id: str) -> bool: ...

    async def resolve(self, secret: str) -> Principal | None: ...


class LedgerSink(Protocol):
    async def write_batch(self, events: Sequence[UsageEvent]) -> None: ...


class UsageStore(Protocol):
    async def summary(
        self, *, tenant_id: str | None, since: datetime, until: datetime
    ) -> UsageSummary: ...


class Storage(Protocol):
    @property
    def auth(self) -> AuthStore: ...

    @property
    def ledger(self) -> LedgerSink: ...

    @property
    def usage(self) -> UsageStore: ...

    async def start(self) -> None: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...
