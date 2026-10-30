"""In-memory storage for tests and dependency-free local runs."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from switchboard.auth.keys import generate_api_key, hash_key, lookup_prefix, verify_key
from switchboard.auth.models import ApiKey, Principal, Role, Tenant
from switchboard.ledger.models import UsageEvent, UsageSummary, UsageSummaryRow


def _percentile(values: list[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return float(ordered[idx])


class MemoryAuthStore:
    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._keys: dict[str, ApiKey] = {}
        self._hashes: dict[str, str] = {}  # key_hash -> key id

    async def create_tenant(self, name: str, tier: str = "standard") -> Tenant:
        if any(t.name == name for t in self._tenants.values()):
            msg = f"tenant '{name}' already exists"
            raise ValueError(msg)
        tenant = Tenant(
            id=f"ten_{uuid.uuid4().hex[:16]}", name=name, tier=tier, created_at=datetime.now(UTC)
        )
        self._tenants[tenant.id] = tenant
        return tenant

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def list_tenants(self) -> list[Tenant]:
        return sorted(self._tenants.values(), key=lambda t: t.created_at)

    async def create_key(
        self,
        tenant_id: str,
        name: str,
        role: Role,
        *,
        rate_limit_rpm: int | None = None,
        rate_limit_tpm: int | None = None,
    ) -> tuple[ApiKey, str]:
        if tenant_id not in self._tenants:
            msg = f"unknown tenant '{tenant_id}'"
            raise ValueError(msg)
        secret = generate_api_key()
        key = ApiKey(
            id=f"key_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            name=name,
            key_prefix=lookup_prefix(secret),
            role=role,
            rate_limit_rpm=rate_limit_rpm,
            rate_limit_tpm=rate_limit_tpm,
            created_at=datetime.now(UTC),
        )
        self._keys[key.id] = key
        self._hashes[hash_key(secret)] = key.id
        return key, secret

    async def list_keys(self, tenant_id: str) -> list[ApiKey]:
        return [k for k in self._keys.values() if k.tenant_id == tenant_id]

    async def revoke_key(self, key_id: str) -> bool:
        key = self._keys.get(key_id)
        if key is None or key.revoked_at is not None:
            return False
        self._keys[key_id] = key.model_copy(update={"revoked_at": datetime.now(UTC)})
        return True

    async def resolve(self, secret: str) -> Principal | None:
        for key_hash, key_id in self._hashes.items():
            if verify_key(secret, key_hash):
                key = self._keys[key_id]
                if key.revoked_at is not None:
                    return None
                tenant = self._tenants[key.tenant_id]
                return Principal(
                    tenant_id=tenant.id,
                    tenant_name=tenant.name,
                    tenant_tier=tenant.tier,
                    api_key_id=key.id,
                    role=key.role,
                    rate_limit_rpm=key.rate_limit_rpm,
                    rate_limit_tpm=key.rate_limit_tpm,
                )
        return None


class MemoryLedger:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def write_batch(self, events: Sequence[UsageEvent]) -> None:
        self.events.extend(events)

    async def summary(
        self, *, tenant_id: str | None, since: datetime, until: datetime
    ) -> UsageSummary:
        selected = [
            e
            for e in self.events
            if since <= e.ts < until and (tenant_id is None or e.tenant_id == tenant_id)
        ]
        by_dep: dict[str | None, list[UsageEvent]] = {}
        for e in selected:
            by_dep.setdefault(e.deployment_id, []).append(e)
        rows = []
        for dep, evs in sorted(by_dep.items(), key=lambda kv: kv[0] or ""):
            totals = [e.total_ms for e in evs]
            rows.append(
                UsageSummaryRow(
                    deployment_id=dep,
                    requests=len(evs),
                    errors=sum(e.status == "error" for e in evs),
                    prompt_tokens=sum(e.prompt_tokens for e in evs),
                    completion_tokens=sum(e.completion_tokens for e in evs),
                    cost_usd=sum((e.cost_usd for e in evs), Decimal(0)),
                    cache_hits=sum(e.cache_status.startswith("hit") for e in evs),
                    avg_total_ms=sum(totals) / len(totals) if totals else None,
                    p95_total_ms=_percentile(totals, 0.95),
                )
            )
        return UsageSummary(tenant_id=tenant_id, since=since, until=until, rows=rows)


class MemoryStorage:
    def __init__(self) -> None:
        self.auth = MemoryAuthStore()
        self._ledger = MemoryLedger()
        self.ledger = self._ledger
        self.usage = self._ledger

    @property
    def events(self) -> list[UsageEvent]:
        return self._ledger.events

    async def start(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None
