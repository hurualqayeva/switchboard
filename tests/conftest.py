from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from switchboard.core.settings import Settings
from switchboard.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", log_level="WARNING")


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
