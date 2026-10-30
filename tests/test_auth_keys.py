from switchboard.auth.keys import (
    generate_api_key,
    hash_key,
    looks_like_key,
    lookup_prefix,
    verify_key,
)
from switchboard.storage.memory import MemoryStorage


def test_generate_and_verify() -> None:
    secret = generate_api_key()
    assert secret.startswith("sb_")
    assert looks_like_key(secret)
    assert not looks_like_key("sb_")
    assert not looks_like_key("Bearer abc")
    assert len(lookup_prefix(secret)) == 11
    h = hash_key(secret)
    assert verify_key(secret, h)
    assert not verify_key(secret + "x", h)
    assert generate_api_key() != secret


async def test_memory_auth_roundtrip() -> None:
    store = MemoryStorage().auth
    tenant = await store.create_tenant("acme", "gold")
    key, secret = await store.create_key(tenant.id, "ci", "developer", rate_limit_rpm=60)
    principal = await store.resolve(secret)
    assert principal is not None
    assert principal.tenant_id == tenant.id
    assert principal.tenant_tier == "gold"
    assert principal.role == "developer"
    assert principal.rate_limit_rpm == 60
    assert principal.has_role("developer", "admin")
    assert not principal.has_role("admin")

    assert await store.resolve("sb_nonsense") is None
    assert [k.id for k in await store.list_keys(tenant.id)] == [key.id]
    assert await store.revoke_key(key.id)
    assert not await store.revoke_key(key.id)
    assert await store.resolve(secret) is None
    assert (await store.get_tenant(tenant.id)) == tenant
    assert await store.get_tenant("nope") is None
