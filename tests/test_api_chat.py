import asyncio
import json
from decimal import Decimal

from httpx import AsyncClient

from switchboard.schemas.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from switchboard.storage.memory import MemoryStorage
from tests.conftest import bearer
from tests.stubs import StubAdapter

CHAT = {"model": "stub-chat", "messages": [{"role": "user", "content": "hi"}]}


async def _events(storage: MemoryStorage, n: int = 1):  # type: ignore[no-untyped-def]
    for _ in range(50):
        if len(storage.events) >= n:
            return storage.events
        await asyncio.sleep(0.02)
    raise AssertionError(f"expected {n} ledger events, got {len(storage.events)}")


async def test_requires_api_key(client: AsyncClient) -> None:
    r = await client.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer("sb_wrong"))
    assert r.status_code == 401


async def test_validation_error_shape(client: AsyncClient, keys: dict[str, str]) -> None:
    r = await client.post(
        "/v1/chat/completions", json={"model": "stub-chat"}, headers=bearer(keys["dev"])
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["param"] == "messages"


async def test_model_not_found(client: AsyncClient, keys: dict[str, str]) -> None:
    r = await client.post(
        "/v1/chat/completions", json={**CHAT, "model": "nope"}, headers=bearer(keys["dev"])
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"
    r = await client.post(
        "/v1/chat/completions",
        json={**CHAT, "model": "disabled-model"},
        headers=bearer(keys["dev"]),
    )
    assert r.status_code == 404


async def test_capability_checks(client: AsyncClient, keys: dict[str, str]) -> None:
    h = bearer(keys["dev"])
    r = await client.post("/v1/chat/completions", json={**CHAT, "model": "stub-embed"}, headers=h)
    assert r.status_code == 400
    r = await client.post(
        "/v1/chat/completions", json={**CHAT, "model": "no-stream", "stream": True}, headers=h
    )
    assert r.status_code == 400
    assert r.json()["error"]["param"] == "stream"
    tools = [{"type": "function", "function": {"name": "f"}}]
    r = await client.post(
        "/v1/chat/completions", json={**CHAT, "model": "no-stream", "tools": tools}, headers=h
    )
    assert r.json()["error"]["param"] == "tools"
    r = await client.post("/v1/chat/completions", json={**CHAT, "n": 2}, headers=h)
    assert r.json()["error"]["param"] == "n"


async def test_chat_completion_and_ledger(
    client: AsyncClient, keys: dict[str, str], storage: MemoryStorage, adapter: StubAdapter
) -> None:
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer(keys["dev"]))
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "stub-chat"
    assert body["choices"][0]["message"]["content"] == "hello from stub"
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert adapter.calls[0][2].model == "stub-model"

    events = await _events(storage)
    e = events[0]
    assert e.tenant_id == keys["tenant_id"]
    assert e.deployment_id == "stub-chat"
    assert e.provider == "openai_compat"
    assert e.status == "ok"
    assert e.stream is False
    assert e.prompt_tokens == 10
    assert e.completion_tokens == 5
    # 10 * 1.00/1M + 5 * 2.00/1M
    assert e.cost_usd == Decimal("0.00002")
    assert e.request_id == r.headers["x-request-id"]
    assert e.ttft_ms is not None
    assert e.total_ms >= 0


async def test_usage_estimated_when_provider_omits_it(
    client: AsyncClient, keys: dict[str, str], adapter: StubAdapter
) -> None:
    adapter.report_usage = False
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer(keys["dev"]))
    usage = r.json()["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0


async def test_provider_error_maps_status_and_ledger(
    client: AsyncClient, keys: dict[str, str], storage: MemoryStorage, adapter: StubAdapter
) -> None:
    adapter.fail = ProviderRateLimitedError(
        "slow", provider="openai_compat", deployment="stub-chat"
    )
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer(keys["dev"]))
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error"
    e = (await _events(storage))[0]
    assert e.status == "error"
    assert e.error_code == "provider_rate_limited"
    assert e.cost_usd == 0

    adapter.fail = ProviderBadRequestError("bad", provider="openai_compat", deployment="stub-chat")
    r = await client.post("/v1/chat/completions", json=CHAT, headers=bearer(keys["dev"]))
    assert r.status_code == 400


def _parse_sse(text: str) -> list[dict]:  # type: ignore[type-arg]
    out = []
    for block in text.strip().split("\n\n"):
        assert block.startswith("data: ")
        payload = block[len("data: ") :]
        out.append({"done": True} if payload == "[DONE]" else json.loads(payload))
    return out


async def test_streaming_sse(
    client: AsyncClient, keys: dict[str, str], storage: MemoryStorage
) -> None:
    r = await client.post(
        "/v1/chat/completions", json={**CHAT, "stream": True}, headers=bearer(keys["dev"])
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-switchboard-deployment"] == "stub-chat"
    events = _parse_sse(r.text)
    assert events[-1] == {"done": True}
    chunks = events[:-1]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "hello from stub"
    # usage chunk is filtered out unless the client asked for it
    assert all(c["choices"] for c in chunks)

    e = (await _events(storage))[0]
    assert e.stream is True
    assert e.status == "ok"
    assert e.prompt_tokens == 10
    assert e.completion_tokens == 5
    assert e.ttft_ms is not None


async def test_streaming_with_usage_option(client: AsyncClient, keys: dict[str, str]) -> None:
    body = {**CHAT, "stream": True, "stream_options": {"include_usage": True}}
    r = await client.post("/v1/chat/completions", json=body, headers=bearer(keys["dev"]))
    chunks = _parse_sse(r.text)[:-1]
    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"]["total_tokens"] == 15


async def test_streaming_error_before_first_chunk_keeps_status(
    client: AsyncClient, keys: dict[str, str], adapter: StubAdapter
) -> None:
    adapter.fail = ProviderUnavailableError(
        "down", provider="openai_compat", deployment="stub-chat"
    )
    r = await client.post(
        "/v1/chat/completions", json={**CHAT, "stream": True}, headers=bearer(keys["dev"])
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_unavailable"


async def test_streaming_error_mid_stream_is_in_band(
    client: AsyncClient, keys: dict[str, str], adapter: StubAdapter, storage: MemoryStorage
) -> None:
    adapter.fail = ProviderUnavailableError(
        "down", provider="openai_compat", deployment="stub-chat"
    )
    adapter.fail_after_chunks = 2
    r = await client.post(
        "/v1/chat/completions", json={**CHAT, "stream": True}, headers=bearer(keys["dev"])
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events[-1] == {"done": True}
    assert "error" in events[-2]
    assert events[-2]["error"]["code"] == "provider_unavailable"
    e = (await _events(storage))[0]
    assert e.status == "error"


async def test_embeddings(
    client: AsyncClient, keys: dict[str, str], storage: MemoryStorage
) -> None:
    r = await client.post(
        "/v1/embeddings",
        json={"model": "stub-embed", "input": ["a", "b"]},
        headers=bearer(keys["dev"]),
    )
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2
    e = (await _events(storage))[0]
    assert e.prompt_tokens == 7
    assert e.completion_tokens == 0
    r = await client.post(
        "/v1/embeddings", json={"model": "stub-chat", "input": "a"}, headers=bearer(keys["dev"])
    )
    assert r.status_code == 400


async def test_list_models(client: AsyncClient, keys: dict[str, str]) -> None:
    r = await client.get("/v1/models", headers=bearer(keys["dev"]))
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert "stub-chat" in ids
    assert "disabled-model" not in ids
