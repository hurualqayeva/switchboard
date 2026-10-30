import json

import httpx
import pytest

from switchboard.catalog.models import Deployment
from switchboard.providers.openai_compat import OpenAICompatAdapter
from switchboard.schemas.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from switchboard.schemas.openai import ChatCompletionRequest, ChatMessage, EmbeddingRequest
from tests.stubs import drain

DEP = Deployment(
    id="local",
    provider="openai_compat",
    model="upstream-name",
    base_url="http://llm/v1",
    api_key_env="K",
)
REQ = ChatCompletionRequest(model="local", messages=[ChatMessage(role="user", content="hi")])


def _adapter(handler) -> OpenAICompatAdapter:  # type: ignore[no-untyped-def]
    return OpenAICompatAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_complete_maps_model_and_sends_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "sk-test")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "upstream-name",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "yo"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    resp = await _adapter(handler).complete(REQ, DEP)
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "upstream-name"
    assert body["stream"] is False
    assert resp.model == "local"
    assert resp.choices[0].message.content == "yo"
    assert resp.usage.total_tokens == 4


async def test_stream_parses_sse_and_requests_usage() -> None:
    chunks = [
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "u",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "He"}}],
        },
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "u",
            "choices": [{"index": 0, "delta": {"content": "llo"}, "finish_reason": "stop"}],
        },
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "u",
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        },
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )

    out = [c async for c in _adapter(handler).stream(REQ, DEP)]
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert "".join(c.choices[0].delta.content or "" for c in out if c.choices) == "Hello"
    assert out[-1].usage is not None
    assert out[-1].usage.total_tokens == 4
    assert all(c.model == "local" for c in out)


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderRateLimitedError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (400, ProviderBadRequestError),
        (422, ProviderBadRequestError),
    ],
)
async def test_http_errors_are_classified(status: int, exc: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    with pytest.raises(exc) as info:
        await _adapter(handler).complete(REQ, DEP)
    assert "boom" in str(info.value)

    with pytest.raises(exc):
        await drain(_adapter(handler).stream(REQ, DEP))


async def test_timeout_and_connection_errors() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderTimeoutError):
        await _adapter(timeout).complete(REQ, DEP)
    with pytest.raises(ProviderUnavailableError):
        await _adapter(refused).complete(REQ, DEP)
    with pytest.raises(ProviderTimeoutError):
        await drain(_adapter(timeout).stream(REQ, DEP))


async def test_embed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "upstream-name",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    resp = await _adapter(handler).embed(EmbeddingRequest(model="local", input="hi"), DEP)
    assert resp.model == "local"
    assert resp.data[0].embedding == [0.1, 0.2]


async def test_missing_base_url() -> None:
    dep = Deployment(id="x", provider="openai_compat", model="m")
    with pytest.raises(Exception, match="no base_url"):
        await _adapter(lambda r: httpx.Response(200)).complete(REQ, dep)
