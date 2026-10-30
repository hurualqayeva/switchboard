import asyncio

from httpx import AsyncClient

from switchboard.storage.memory import MemoryStorage
from tests.conftest import bearer

CHAT = {"model": "stub-chat", "messages": [{"role": "user", "content": "hi"}]}


async def test_admin_requires_admin_role(client: AsyncClient, keys: dict[str, str]) -> None:
    r = await client.get("/admin/tenants", headers=bearer(keys["dev"]))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_role"
    r = await client.get("/admin/tenants")
    assert r.status_code == 401


async def test_tenant_and_key_lifecycle(client: AsyncClient, keys: dict[str, str]) -> None:
    admin = bearer(keys["admin"])
    r = await client.post("/admin/tenants", json={"name": "newco", "tier": "gold"}, headers=admin)
    assert r.status_code == 201
    tenant = r.json()
    assert tenant["name"] == "newco"
    assert tenant["id"].startswith("ten_")

    r = await client.post("/admin/tenants", json={"name": "newco"}, headers=admin)
    assert r.status_code == 400

    r = await client.post(
        "/admin/keys",
        json={"tenant_id": tenant["id"], "name": "svc", "role": "developer", "rate_limit_rpm": 10},
        headers=admin,
    )
    assert r.status_code == 201
    created = r.json()
    secret = created["secret"]
    assert secret.startswith("sb_")
    assert "key_hash" not in created["key"]

    # the new key works and is scoped to its tenant
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer(secret))
    assert r.status_code == 200

    r = await client.get(f"/admin/tenants/{tenant['id']}/keys", headers=admin)
    assert [k["id"] for k in r.json()] == [created["key"]["id"]]

    r = await client.delete(f"/admin/keys/{created['key']['id']}", headers=admin)
    assert r.status_code == 204
    r = await client.delete(f"/admin/keys/{created['key']['id']}", headers=admin)
    assert r.status_code == 400
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer(secret))
    assert r.status_code == 401

    r = await client.post(
        "/admin/keys", json={"tenant_id": "ten_missing", "name": "x"}, headers=admin
    )
    assert r.status_code == 400

    r = await client.get("/admin/tenants", headers=admin)
    assert {t["name"] for t in r.json()} >= {"acme", "newco"}


async def test_usage_summary_is_per_tenant(
    client: AsyncClient, keys: dict[str, str], storage: MemoryStorage
) -> None:
    for _ in range(3):
        await client.post("/v1/chat/completions", json=CHAT, headers=bearer(keys["dev"]))
    await client.post("/v1/chat/completions", json=CHAT, headers=bearer(keys["other_dev"]))
    for _ in range(50):
        if len(storage.events) >= 4:
            break
        await asyncio.sleep(0.02)

    admin = bearer(keys["admin"])
    r = await client.get("/admin/usage", params={"tenant_id": keys["tenant_id"]}, headers=admin)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["deployment_id"] == "stub-chat"
    assert rows[0]["requests"] == 3
    assert rows[0]["prompt_tokens"] == 30
    assert rows[0]["p95_total_ms"] is not None

    r = await client.get("/admin/usage", headers=admin)
    assert r.json()["rows"][0]["requests"] == 4

    r = await client.get(
        "/admin/usage",
        params={"since": "2020-01-01T00:00:00", "until": "2020-02-01T00:00:00"},
        headers=admin,
    )
    assert r.json()["rows"] == []
