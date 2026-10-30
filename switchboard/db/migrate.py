"""Tiny forward-only SQL migrator plus monthly partition management for the ledger.

Alembic would be the conventional choice, but the schema is small and owning the migrator
keeps the dependency list short and the behaviour obvious: files in ``migrations/`` are
applied in name order inside one transaction each and recorded in ``schema_migrations``.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import asyncpg

from switchboard.core.logging import get_logger

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
log = get_logger(__name__)

Conn = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy


async def run_migrations(conn: Conn) -> list[str]:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
    done: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
        log.info("db.migrated", version=version)
        done.append(version)
    return done


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


async def ensure_usage_partitions(conn: Conn, *, months_ahead: int = 1) -> list[str]:
    """Create usage_events partitions for the current month and ``months_ahead`` more."""
    created: list[str] = []
    start = _month_start(datetime.now(UTC).date())
    for _ in range(months_ahead + 1):
        end = _next_month(start)
        name = f"usage_events_{start:%Y%m}"
        exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name)
        if not exists:
            await conn.execute(
                f"CREATE TABLE {name} PARTITION OF usage_events "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
            )
            log.info("db.partition_created", partition=name)
            created.append(name)
        start = end
    return created
