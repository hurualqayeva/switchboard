"""Postgres storage backed by asyncpg."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import asyncpg

from switchboard.auth.keys import (
    generate_api_key,
    hash_key,
    looks_like_key,
    lookup_prefix,
    verify_key,
)
from switchboard.auth.models import ApiKey, Principal, Role, Tenant
from switchboard.db.migrate import ensure_usage_partitions, run_migrations
from switchboard.db.pool import create_pool
from switchboard.ledger.models import UsageEvent, UsageSummary, UsageSummaryRow


class PostgresAuthStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_tenant(self, name: str, tier: str = "standard") -> Tenant:
        row = await self._pool.fetchrow(
            "INSERT INTO tenants (id, name, tier) VALUES ($1, $2, $3) RETURNING *",
            f"ten_{uuid.uuid4().hex[:16]}",
            name,
            tier,
        )
        assert row is not None
        return Tenant(**dict(row))

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        row = await self._pool.fetchrow("SELECT * FROM tenants WHERE id = $1", tenant_id)
        return Tenant(**dict(row)) if row else None

    async def list_tenants(self) -> list[Tenant]:
        rows = await self._pool.fetch("SELECT * FROM tenants ORDER BY created_at")
        return [Tenant(**dict(r)) for r in rows]

    async def create_key(
        self,
        tenant_id: str,
        name: str,
        role: Role,
        *,
        rate_limit_rpm: int | None = None,
        rate_limit_tpm: int | None = None,
    ) -> tuple[ApiKey, str]:
        secret = generate_api_key()
        row = await self._pool.fetchrow(
            """
            INSERT INTO api_keys (id, tenant_id, name, key_prefix, key_hash, role,
                                  rate_limit_rpm, rate_limit_tpm)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, tenant_id, name, key_prefix, role, rate_limit_rpm, rate_limit_tpm,
                      created_at, revoked_at
            """,
            f"key_{uuid.uuid4().hex[:16]}",
            tenant_id,
            name,
            lookup_prefix(secret),
            hash_key(secret),
            role,
            rate_limit_rpm,
            rate_limit_tpm,
        )
        assert row is not None
        return ApiKey(**dict(row)), secret

    async def list_keys(self, tenant_id: str) -> list[ApiKey]:
        rows = await self._pool.fetch(
            """
            SELECT id, tenant_id, name, key_prefix, role, rate_limit_rpm, rate_limit_tpm,
                   created_at, revoked_at
            FROM api_keys WHERE tenant_id = $1 ORDER BY created_at
            """,
            tenant_id,
        )
        return [ApiKey(**dict(r)) for r in rows]

    async def revoke_key(self, key_id: str) -> bool:
        result = await self._pool.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL", key_id
        )
        return result.endswith(" 1")

    async def resolve(self, secret: str) -> Principal | None:
        if not looks_like_key(secret):
            return None
        rows = await self._pool.fetch(
            """
            SELECT k.id AS api_key_id, k.key_hash, k.role, k.rate_limit_rpm, k.rate_limit_tpm,
                   k.revoked_at, t.id AS tenant_id, t.name AS tenant_name, t.tier AS tenant_tier
            FROM api_keys k JOIN tenants t ON t.id = k.tenant_id
            WHERE k.key_prefix = $1
            """,
            lookup_prefix(secret),
        )
        for row in rows:
            if verify_key(secret, row["key_hash"]):
                if row["revoked_at"] is not None:
                    return None
                return Principal(
                    tenant_id=row["tenant_id"],
                    tenant_name=row["tenant_name"],
                    tenant_tier=row["tenant_tier"],
                    api_key_id=row["api_key_id"],
                    role=row["role"],
                    rate_limit_rpm=row["rate_limit_rpm"],
                    rate_limit_tpm=row["rate_limit_tpm"],
                )
        return None


_INSERT_EVENT = """
INSERT INTO usage_events (
    ts, request_id, tenant_id, api_key_id, requested_model, deployment_id, provider,
    task_class, route_mode, route_reason, experiment_id, cache_status, stream,
    prompt_tokens, completion_tokens, cost_usd, ttft_ms, total_ms, status, error_code
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
"""


class PostgresLedger:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def write_batch(self, events: Sequence[UsageEvent]) -> None:
        args = [
            (
                e.ts,
                e.request_id,
                e.tenant_id,
                e.api_key_id,
                e.requested_model,
                e.deployment_id,
                e.provider,
                e.task_class,
                e.route_mode,
                e.route_reason,
                e.experiment_id,
                e.cache_status,
                e.stream,
                e.prompt_tokens,
                e.completion_tokens,
                e.cost_usd,
                e.ttft_ms,
                e.total_ms,
                e.status,
                e.error_code,
            )
            for e in events
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(_INSERT_EVENT, args)

    async def summary(
        self, *, tenant_id: str | None, since: datetime, until: datetime
    ) -> UsageSummary:
        rows = await self._pool.fetch(
            """
            SELECT deployment_id,
                   count(*)::int                                   AS requests,
                   count(*) FILTER (WHERE status = 'error')::int   AS errors,
                   coalesce(sum(prompt_tokens), 0)::int            AS prompt_tokens,
                   coalesce(sum(completion_tokens), 0)::int        AS completion_tokens,
                   coalesce(sum(cost_usd), 0)                      AS cost_usd,
                   count(*) FILTER (WHERE cache_status LIKE 'hit%')::int AS cache_hits,
                   avg(total_ms)::float                            AS avg_total_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)::float AS p95_total_ms
            FROM usage_events
            WHERE ts >= $1 AND ts < $2 AND ($3::text IS NULL OR tenant_id = $3)
            GROUP BY deployment_id
            ORDER BY cost_usd DESC
            """,
            since,
            until,
            tenant_id,
        )
        return UsageSummary(
            tenant_id=tenant_id,
            since=since,
            until=until,
            rows=[
                UsageSummaryRow(**{**dict(r), "cost_usd": Decimal(str(r["cost_usd"]))})
                for r in rows
            ],
        )


class PostgresStorage:
    def __init__(self, dsn: str, *, auto_migrate: bool = True) -> None:
        self._dsn = dsn
        self._auto_migrate = auto_migrate
        self._pool: asyncpg.Pool | None = None
        self.auth: PostgresAuthStore
        self.ledger: PostgresLedger
        self.usage: PostgresLedger

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "storage not started"
        return self._pool

    async def start(self) -> None:
        self._pool = await create_pool(self._dsn)
        async with self._pool.acquire() as conn:
            if self._auto_migrate:
                await run_migrations(conn)
            await ensure_usage_partitions(conn)
        self.auth = PostgresAuthStore(self._pool)
        ledger = PostgresLedger(self._pool)
        self.ledger = ledger
        self.usage = ledger

    async def ping(self) -> bool:
        if self._pool is None:
            return False
        try:
            return bool(await self._pool.fetchval("SELECT 1") == 1)
        except (asyncpg.PostgresError, OSError):
            return False

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
