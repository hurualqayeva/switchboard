from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from botocore.exceptions import ClientError

from switchboard.catalog.models import Deployment
from switchboard.providers.bedrock import (
    BedrockAdapter,
    from_converse_response,
    to_converse_request,
)
from switchboard.schemas.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from switchboard.schemas.openai import (
    ChatCompletionRequest,
    ChatMessage,
    FunctionCall,
    FunctionDefinition,
    Tool,
    ToolCall,
)
from tests.stubs import drain

DEP = Deployment(
    id="br",
    provider="bedrock",
    model="anthropic.claude-x",
    region="eu-west-1",
    max_output_tokens=777,
)


def _req(**kw) -> ChatCompletionRequest:  # type: ignore[no-untyped-def]
    base = {"model": "br", "messages": [ChatMessage(role="user", content="hi")]}
    return ChatCompletionRequest(**{**base, **kw})


def test_request_translation() -> None:
    req = _req(
        messages=[
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="q"),
            ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(id="t1", function=FunctionCall(name="f", arguments='{"x":1}'))
                ],
            ),
            ChatMessage(role="tool", tool_call_id="t1", content="r1"),
        ],
        tools=[
            Tool(
                function=FunctionDefinition(
                    name="f", description="d", parameters={"type": "object"}
                )
            )
        ],
        tool_choice="auto",
        temperature=0.1,
        top_p=0.9,
        stop="X",
    )
    body = to_converse_request(req, DEP)
    assert body["modelId"] == "anthropic.claude-x"
    assert body["system"] == [{"text": "sys"}]
    assert body["inferenceConfig"] == {
        "maxTokens": 777,
        "temperature": 0.1,
        "topP": 0.9,
        "stopSequences": ["X"],
    }
    assert body["toolConfig"]["toolChoice"] == {"auto": {}}
    assert body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": {"type": "object"}}
    msgs = body["messages"]
    assert msgs[1]["content"][0]["toolUse"] == {"toolUseId": "t1", "name": "f", "input": {"x": 1}}
    assert msgs[2]["content"][0]["toolResult"]["toolUseId"] == "t1"


def test_tool_choice_none_drops_tool_config() -> None:
    body = to_converse_request(
        _req(tools=[Tool(function=FunctionDefinition(name="f"))], tool_choice="none"), DEP
    )
    assert "toolConfig" not in body


def test_response_translation() -> None:
    data = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "sure"},
                    {"toolUse": {"toolUseId": "tu", "name": "f", "input": {"k": "v"}}},
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 4, "outputTokens": 2},
    }
    resp = from_converse_response(data, DEP)
    assert resp.model == "br"
    assert resp.choices[0].message.content == "sure"
    assert resp.choices[0].finish_reason == "tool_calls"
    assert resp.choices[0].message.tool_calls[0].function.arguments == '{"k": "v"}'  # type: ignore[index]
    assert resp.usage.total_tokens == 6


class FakeClient:
    def __init__(
        self, *, converse=None, stream_events=None, error: Exception | None = None
    ) -> None:  # type: ignore[no-untyped-def]
        self._converse = converse
        self._events = stream_events or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._converse

    async def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error:
            raise self._error

        async def gen() -> AsyncIterator[dict[str, Any]]:
            for e in self._events:
                yield e

        return {"stream": gen()}


def _adapter(client: FakeClient) -> tuple[BedrockAdapter, list[tuple[str, float]]]:
    seen: list[tuple[str, float]] = []

    @asynccontextmanager
    async def factory(region: str, timeout_s: float):  # type: ignore[no-untyped-def]
        seen.append((region, timeout_s))
        yield client

    return BedrockAdapter(client_factory=factory), seen


async def test_complete_uses_region_and_timeout() -> None:
    client = FakeClient(
        converse={
            "output": {"message": {"content": [{"text": "hey"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }
    )
    adapter, seen = _adapter(client)
    resp = await adapter.complete(_req(), DEP)
    assert seen == [("eu-west-1", 60.0)]
    assert resp.choices[0].message.content == "hey"


async def test_stream_translation() -> None:
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "a"}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "b"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"toolUseId": "tu1", "name": "f"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"z":'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "2}"}}}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 3}}},
    ]
    adapter, _ = _adapter(FakeClient(stream_events=events))
    chunks = [c async for c in adapter.stream(_req(), DEP)]
    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices) == "ab"
    args = "".join(
        c.choices[0].delta.tool_calls[0].function.arguments or ""  # type: ignore[index, union-attr]
        for c in chunks
        if c.choices and c.choices[0].delta.tool_calls
    )
    assert args == '{"z":2}'
    assert [
        c.choices[0].finish_reason for c in chunks if c.choices and c.choices[0].finish_reason
    ] == ["tool_calls"]
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 8


async def test_stream_exception_event() -> None:
    adapter, _ = _adapter(FakeClient(stream_events=[{"throttlingException": {"message": "slow"}}]))
    with pytest.raises(ProviderUnavailableError, match="throttlingException"):
        await drain(adapter.stream(_req(), DEP))


def _client_error(code: str, status: int = 400) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "m"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "Converse",
    )


@pytest.mark.parametrize(
    ("code", "exc"),
    [
        ("ThrottlingException", ProviderRateLimitedError),
        ("AccessDeniedException", ProviderAuthError),
        ("ValidationException", ProviderBadRequestError),
        ("InternalServerException", ProviderUnavailableError),
    ],
)
async def test_client_errors_classified(code: str, exc: type[Exception]) -> None:
    adapter, _ = _adapter(FakeClient(error=_client_error(code)))
    with pytest.raises(exc):
        await adapter.complete(_req(), DEP)
    with pytest.raises(exc):
        await drain(adapter.stream(_req(), DEP))


async def test_missing_region() -> None:
    adapter, _ = _adapter(FakeClient(converse={}))
    with pytest.raises(ProviderError, match="no region"):
        await adapter.complete(_req(), Deployment(id="x", provider="bedrock", model="m"))
