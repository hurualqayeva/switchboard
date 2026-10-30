from httpx import AsyncClient

from switchboard import __version__


async def test_healthz(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


async def test_readyz(client: AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_request_id_is_generated(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32


async def test_request_id_is_propagated(client: AsyncClient) -> None:
    r = await client.get("/healthz", headers={"x-request-id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
