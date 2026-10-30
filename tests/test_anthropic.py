import json

import httpx
import pytest

from switchboard.catalog.models import Deployment
from switchboard.providers.anthropic import (
    AnthropicAdapter,
    from_anthropic_response,
    to_anthropic_request,
)
from switchboard.schemas.errors import (
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitedError,
)
from switchboard.schemas.openai import (
    ChatCompletionRequest,
    ChatMessage,
    ContentPart,
    EmbeddingRequest,
    FunctionCall,
    FunctionDefinition,
    ImageUrl,
    NamedToolChoice,
    Tool,
    ToolCall,
)
from tests.stubs import drain

DEP = Deployment(
    id="claude",
    provider="anthropic",
    model="claude-x",
    base_url="http://anthropic",
    api_key_env="ANTHROPIC_TEST_KEY",
    max_output_tokens=1000,
)


def _req(**kw) -> ChatCompletionRequest:  # type: ignore[no-untyped-def]
    base = {"model": "claude", "messages": [ChatMessage(role="user", content="hi")]}
    return ChatCompletionRequest(**{**base, **kw})


def test_request_translation_system_tools_and_tool_results() -> None:
    req = _req(
        messages=[
            ChatMessage(role="system", content="You are terse."),
            ChatMessage(role="developer", content="Answer in English."),
            ChatMessage(role="user", content="weather?"),
            ChatMessage(
                role="assistant",
                content="checking",
                tool_calls=[
                    ToolCall(
                        id="t1",
                        function=FunctionCall(name="get_weather", arguments='{"city":"Baku"}'),
                    ),
                    ToolCall(id="t2", function=FunctionCall(name="get_time", arguments="not json")),
                ],
            ),
            ChatMessage(role="tool", tool_call_id="t1", content="sunny"),
            ChatMessage(role="tool", tool_call_id="t2", content="noon"),
            ChatMessage(role="user", content="thanks"),
        ],
        tools=[
            Tool(function=FunctionDefinition(name="get_weather", parameters={"type": "object"}))
        ],
        tool_choice="required",
        temperature=0.2,
        stop=["END"],
        max_tokens=50,
        user="u1",
    )
    body = to_anthropic_request(req, DEP)
    assert body["system"] == "You are terse.\n\nAnswer in English."
    assert body["max_tokens"] == 50
    assert body["temperature"] == 0.2
    assert body["stop_sequences"] == ["END"]
    assert body["metadata"] == {"user_id": "u1"}
    assert body["tool_choice"] == {"type": "any"}
    assert body["tools"][0]["input_schema"] == {"type": "object"}
    msgs = body["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "user"]
    assistant = msgs[1]["content"]
    assert assistant[0] == {"type": "text", "text": "checking"}
    assert assistant[1]["input"] == {"city": "Baku"}
    assert assistant[2]["input"] == {"_raw": "not json"}
    # both tool results merged into one user turn
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["t1", "t2"]


def test_request_translation_defaults_and_named_tool() -> None:
    body = to_anthropic_request(
        _req(
            tools=[Tool(function=FunctionDefinition(name="f"))],
            tool_choice=NamedToolChoice(function={"name": "f"}),
        ),
        DEP,
    )
    assert body["max_tokens"] == 1000  # min(deployment max, default 4096)
    assert body["tool_choice"] == {"type": "tool", "name": "f"}
    body2 = to_anthropic_request(
        _req(tools=[Tool(function=FunctionDefinition(name="f"))], tool_choice="none"), DEP
    )
    assert body2["tool_choice"] == {"type": "none"}


def test_image_parts() -> None:
    req = _req(
        messages=[
            ChatMessage(
                role="user",
                content=[
                    ContentPart(type="text", text="what is this"),
                    ContentPart(
                        type="image_url", image_url=ImageUrl(url="data:image/png;base64,AAAA")
                    ),
                    ContentPart(type="image_url", image_url=ImageUrl(url="https://x/y.jpg")),
                ],
            )
        ]
    )
    content = to_anthropic_request(req, DEP)["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "what is this"}
    assert content[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert content[2]["source"] == {"type": "url", "url": "https://x/y.jpg"}


def test_response_translation_with_tool_use() -> None:
    data = {
        "id": "msg_1",
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "tu_1", "name": "get_weather", "input": {"city": "Baku"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    resp = from_anthropic_response(data, DEP)
    assert resp.id == "msg_1"
    assert resp.model == "claude"
    choice = resp.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Let me check."
    assert choice.message.tool_calls is not None
    assert choice.message.tool_calls[0].function.arguments == '{"city": "Baku"}'
    assert resp.usage.total_tokens == 19
    assert (
        from_anthropic_response({"content": [], "stop_reason": "max_tokens"}, DEP)
        .choices[0]
        .finish_reason
        == "length"
    )


def _adapter(handler) -> AnthropicAdapter:  # type: ignore[no-untyped-def]
    return AnthropicAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_missing_key_is_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_TEST_KEY", raising=False)
    with pytest.raises(ProviderError, match="no API key"):
        await _adapter(lambda r: httpx.Response(200)).complete(_req(), DEP)


async def test_complete_sends_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "ak")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-api-key"] = request.headers["x-api-key"]
        seen["version"] = request.headers["anthropic-version"]
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "id": "m",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    resp = await _adapter(handler).complete(_req(), DEP)
    assert seen == {
        "x-api-key": "ak",
        "version": "2023-06-01",
        "url": "http://anthropic/v1/messages",
    }
    assert resp.choices[0].message.content == "ok"


async def test_stream_translates_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "ak")
    events = [
        (
            "message_start",
            {"type": "message_start", "message": {"id": "msg_s", "usage": {"input_tokens": 9}}},
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi "},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "there"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "tu_9", "name": "f"},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"a":'},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": "1}"},
            },
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 6},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    sse = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=sse.encode())

    chunks = [c async for c in _adapter(handler).stream(_req(), DEP)]
    assert chunks[0].id == "msg_s"
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == "Hi there"
    tool_chunks = [c for c in chunks if c.choices and c.choices[0].delta.tool_calls]
    assert tool_chunks[0].choices[0].delta.tool_calls[0].id == "tu_9"  # type: ignore[index]
    assert tool_chunks[0].choices[0].delta.tool_calls[0].function.name == "f"  # type: ignore[index, union-attr]
    args = "".join(
        c.choices[0].delta.tool_calls[0].function.arguments or ""  # type: ignore[index, union-attr]
        for c in tool_chunks
    )
    assert args == '{"a":1}'
    finish = [
        c.choices[0].finish_reason for c in chunks if c.choices and c.choices[0].finish_reason
    ]
    assert finish == ["tool_calls"]
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens == 9
    assert chunks[-1].usage.completion_tokens == 6


async def test_stream_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "ak")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"type": "rate_limit_error", "message": "slow"}})

    with pytest.raises(ProviderRateLimitedError):
        await drain(_adapter(handler).stream(_req(), DEP))


async def test_embed_unsupported() -> None:
    with pytest.raises(ProviderBadRequestError):
        await _adapter(lambda r: httpx.Response(200)).embed(
            EmbeddingRequest(model="claude", input="x"), DEP
        )
