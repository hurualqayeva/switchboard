"""asyncpg connection pool helpers."""

import asyncpg


async def create_pool(dsn: str, *, min_size: int = 2, max_size: int = 16) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
        server_settings={"application_name": "switchboard"},
    )
