import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

if sys.platform == "win32":
    # testcontainers' reaper sidecar cannot bind on Docker Desktop for Windows; the
    # integration fixture stops its container explicitly instead.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from switchboard.core.settings import Settings
from switchboard.main import create_app
from switchboard.storage.memory import MemoryStorage
from tests.stubs import StubAdapter, stub_registry

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        log_level="WARNING",
        storage="memory",
        catalog_path=str(FIXTURES / "catalog.yaml"),
        ledger_flush_interval_s=0.05,
    )


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def adapter() -> StubAdapter:
    return StubAdapter()


@pytest.fixture
async def app(settings: Settings, storage: MemoryStorage, adapter: StubAdapter):  # type: ignore[no-untyped-def]
    application = create_app(settings, storage=storage, registry=stub_registry(adapter))
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def keys(storage: MemoryStorage) -> dict[str, str]:
    """An admin key and a developer key for one tenant, plus a key for a second tenant."""
    tenant = await storage.auth.create_tenant("acme")
    _, admin = await storage.auth.create_key(tenant.id, "admin", "admin")
    _, dev = await storage.auth.create_key(tenant.id, "dev", "developer")
    other = await storage.auth.create_tenant("other")
    _, other_dev = await storage.auth.create_key(other.id, "dev", "developer")
    return {"admin": admin, "dev": dev, "other_dev": other_dev, "tenant_id": tenant.id}


def bearer(secret: str) -> dict[str, str]:
    return {"authorization": f"Bearer {secret}"}
