"""Runs against a throwaway Postgres container. Skipped when Docker is unavailable."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from switchboard.ledger.models import UsageEvent
from switchboard.storage.postgres import PostgresStorage

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn() -> AsyncIterator[str]:  # type: ignore[misc]
    try:
        try:
            from testcontainers.community.postgres import PostgresContainer
        except ImportError:  # older testcontainers
            from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine", driver=None)
        container.start()
    except Exception as exc:  # docker missing or not running
        pytest.skip(f"docker unavailable: {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
async def pg(pg_dsn: str) -> AsyncIterator[PostgresStorage]:
    storage = PostgresStorage(pg_dsn, auto_migrate=True)
    await storage.start()
    try:
        yield storage
    finally:
        await storage.close()


async def test_migrations_and_partitions(pg: PostgresStorage) -> None:
    versions = [r["version"] for r in await pg.pool.fetch("SELECT version FROM schema_migrations")]
    assert versions == ["0001_init"]
    this_month = datetime.now(UTC).strftime("usage_events_%Y%m")
    exists = await pg.pool.fetchval("SELECT to_regclass($1) IS NOT NULL", this_month)
    assert exists
    # starting again is idempotent
    again = PostgresStorage(pg._dsn, auto_migrate=True)
    await again.start()
    await again.close()
    assert await pg.ping()


async def test_auth_store(pg: PostgresStorage) -> None:
    tenant = await pg.auth.create_tenant(f"acme-{datetime.now().timestamp()}", "gold")
    key, secret = await pg.auth.create_key(tenant.id, "ci", "developer", rate_limit_tpm=1000)
    principal = await pg.auth.resolve(secret)
    assert principal is not None
    assert principal.tenant_id == tenant.id
    assert principal.role == "developer"
    assert principal.rate_limit_tpm == 1000
    assert await pg.auth.resolve("sb_" + "x" * 40) is None
    assert await pg.auth.resolve("garbage") is None
    assert [k.id for k in await pg.auth.list_keys(tenant.id)] == [key.id]
    assert await pg.auth.revoke_key(key.id)
    assert not await pg.auth.revoke_key(key.id)
    assert await pg.auth.resolve(secret) is None
    assert (await pg.auth.get_tenant(tenant.id)) is not None
    assert tenant.id in {t.id for t in await pg.auth.list_tenants()}

    with pytest.raises(Exception, match="duplicate key"):
        await pg.auth.create_tenant(tenant.name)


async def test_ledger_write_and_summary(pg: PostgresStorage) -> None:
    tenant = await pg.auth.create_tenant(f"ledger-{datetime.now().timestamp()}")
    now = datetime.now(UTC)
    events = [
        UsageEvent(
            ts=now,
            request_id=f"r{i}",
            tenant_id=tenant.id,
            api_key_id=None,
            requested_model="m",
            deployment_id="dep-a" if i < 3 else "dep-b",
            provider="openai_compat",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=Decimal("0.00002"),
            total_ms=100 + i,
            status="error" if i == 4 else "ok",
            cache_status="hit_exact" if i == 0 else "miss",
        )
        for i in range(5)
    ]
    await pg.ledger.write_batch(events)
    summary = await pg.usage.summary(
        tenant_id=tenant.id, since=now - timedelta(minutes=1), until=now + timedelta(minutes=1)
    )
    rows = {r.deployment_id: r for r in summary.rows}
    assert rows["dep-a"].requests == 3
    assert rows["dep-a"].cache_hits == 1
    assert rows["dep-b"].errors == 1
    assert rows["dep-a"].prompt_tokens == 30
    assert rows["dep-a"].cost_usd == Decimal("0.00006")
    assert summary.total_cost_usd == Decimal("0.00010")
    assert rows["dep-a"].p95_total_ms is not None
