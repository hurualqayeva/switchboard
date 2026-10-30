"""Adapter for Amazon Bedrock through the Converse and ConverseStream APIs.

Converse gives one request shape for every Bedrock model family, so this adapter does not
need per-model prompt formats. Credentials come from the default AWS chain (IRSA on EKS,
a profile locally); the catalog only names the region.
"""

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError

from switchboard.catalog.models import Deployment, ProviderKind
from switchboard.providers.common import (
    ChunkBuilder,
    map_stop_reason,
    parse_tool_arguments,
    system_text,
)
from switchboard.schemas.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    EmbeddingRequest,
    EmbeddingResponse,
    FunctionCall,
    NamedToolChoice,
    ToolCall,
    Usage,
    new_completion_id,
)


class BedrockClient(Protocol):
    async def converse(self, **kwargs: Any) -> dict[str, Any]: ...

    async def converse_stream(self, **kwargs: Any) -> dict[str, Any]: ...


ClientFactory = Callable[[str, float], AbstractAsyncContextManager[BedrockClient]]


# -- request translation -----------------------------------------------------------------


def _user_content(msg: ChatMessage) -> list[dict[str, Any]]:
    if isinstance(msg.content, str) or msg.content is None:
        return [{"text": msg.content or ""}]
    blocks: list[dict[str, Any]] = []
    for part in msg.content:
        if part.type == "text":
            blocks.append({"text": part.text or ""})
        # Converse wants raw image bytes; URL images are not forwarded in phase 1.
    return blocks or [{"text": ""}]


def to_converse_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role in ("system", "developer"):
            continue
        if msg.role == "user":
            out.append({"role": "user", "content": _user_content(msg)})
        elif msg.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if text := msg.text():
                blocks.append({"text": text})
            for tc in msg.tool_calls or []:
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": tc.id,
                            "name": tc.function.name,
                            "input": parse_tool_arguments(tc.function.arguments),
                        }
                    }
                )
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif msg.role == "tool":
            block = {
                "toolResult": {
                    "toolUseId": msg.tool_call_id or "",
                    "content": [{"text": msg.text()}],
                }
            }
            last = out[-1] if out else None
            if last is not None and last["role"] == "user" and "toolResult" in last["content"][0]:
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def to_converse_request(req: ChatCompletionRequest, deployment: Deployment) -> dict[str, Any]:
    inference: dict[str, Any] = {
        "maxTokens": req.effective_max_tokens or deployment.max_output_tokens,
    }
    if req.temperature is not None:
        inference["temperature"] = req.temperature
    if req.top_p is not None:
        inference["topP"] = req.top_p
    if req.stop_sequences:
        inference["stopSequences"] = req.stop_sequences
    body: dict[str, Any] = {
        "modelId": deployment.model,
        "messages": to_converse_messages(req.messages),
        "inferenceConfig": inference,
    }
    if system := system_text(req.messages):
        body["system"] = [{"text": system}]
    if req.tools:
        tool_config: dict[str, Any] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": t.function.name,
                        "description": t.function.description or t.function.name,
                        "inputSchema": {"json": t.function.parameters},
                    }
                }
                for t in req.tools
            ]
        }
        choice = req.tool_choice
        if isinstance(choice, NamedToolChoice):
            tool_config["toolChoice"] = {"tool": {"name": choice.function["name"]}}
        elif choice == "required":
            tool_config["toolChoice"] = {"any": {}}
        elif choice == "auto":
            tool_config["toolChoice"] = {"auto": {}}
        if choice != "none":
            body["toolConfig"] = tool_config
    return body


# -- response translation ----------------------------------------------------------------


def from_converse_response(data: dict[str, Any], deployment: Deployment) -> ChatCompletionResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("output", {}).get("message", {}).get("content", []):
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            tool_calls.append(
                ToolCall(
                    id=tu["toolUseId"],
                    function=FunctionCall(
                        name=tu["name"], arguments=json.dumps(tu.get("input", {}))
                    ),
                )
            )
    usage = data.get("usage", {})
    message = ChatMessage(
        role="assistant",
        content="".join(text_parts) if text_parts else None,
        tool_calls=tool_calls or None,
    )
    return ChatCompletionResponse(
        model=deployment.id,
        choices=[
            Choice(index=0, message=message, finish_reason=map_stop_reason(data.get("stopReason")))
        ],
        usage=Usage.of(int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))),
    )


# -- adapter -----------------------------------------------------------------------------


def _default_client_factory(session: aioboto3.Session) -> ClientFactory:
    def factory(region: str, timeout_s: float) -> AbstractAsyncContextManager[BedrockClient]:
        config = Config(
            read_timeout=timeout_s,
            connect_timeout=5,
            retries={"max_attempts": 0},  # the executor owns retries and fallback
        )
        return session.client("bedrock-runtime", region_name=region, config=config)  # type: ignore[no-any-return]

    return factory


class BedrockAdapter:
    kind: ProviderKind = "bedrock"

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._factory = client_factory or _default_client_factory(aioboto3.Session())

    def _classify(self, exc: Exception, deployment: Deployment) -> ProviderError:
        kwargs: dict[str, Any] = {"provider": self.kind, "deployment": deployment.id}
        if isinstance(exc, ReadTimeoutError):
            return ProviderTimeoutError(f"timeout after {deployment.timeout_s}s", **kwargs)
        if isinstance(exc, ClientError):
            err = exc.response.get("Error", {})
            code = err.get("Code", "")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            message = f"bedrock {code}: {err.get('Message', '')}"
            kwargs["upstream_status"] = status
            if code in (
                "ThrottlingException",
                "TooManyRequestsException",
                "ServiceQuotaExceededException",
            ):
                return ProviderRateLimitedError(message, **kwargs)
            if code in (
                "AccessDeniedException",
                "UnrecognizedClientException",
                "ExpiredTokenException",
            ):
                return ProviderAuthError(message, **kwargs)
            if code in ("ValidationException", "ResourceNotFoundException"):
                return ProviderBadRequestError(message, **kwargs)
            if code in ("ModelTimeoutException",):
                return ProviderTimeoutError(message, **kwargs)
            return ProviderUnavailableError(message, **kwargs)
        if isinstance(exc, BotoCoreError):
            return ProviderUnavailableError(f"bedrock client error: {exc}", **kwargs)
        return ProviderError(f"bedrock error: {exc}", **kwargs)

    def _region(self, deployment: Deployment) -> str:
        if not deployment.region:
            raise ProviderError(
                f"deployment '{deployment.id}' has no region",
                provider=self.kind,
                deployment=deployment.id,
            )
        return deployment.region

    async def complete(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> ChatCompletionResponse:
        body = to_converse_request(req, deployment)
        try:
            async with self._factory(self._region(deployment), deployment.timeout_s) as client:
                data = await client.converse(**body)
        except (ClientError, BotoCoreError) as exc:
            raise self._classify(exc, deployment) from exc
        return from_converse_response(data, deployment)

    async def stream(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> AsyncIterator[ChatCompletionChunk]:
        body = to_converse_request(req, deployment)
        builder = ChunkBuilder(new_completion_id(), deployment.id)
        try:
            async with self._factory(self._region(deployment), deployment.timeout_s) as client:
                resp = await client.converse_stream(**body)
                async for event in resp["stream"]:
                    if "contentBlockStart" in event:
                        start = event["contentBlockStart"]
                        tool = start.get("start", {}).get("toolUse")
                        if tool:
                            yield builder.tool_start(
                                int(start.get("contentBlockIndex", 0)),
                                tool["toolUseId"],
                                tool["name"],
                            )
                    elif "contentBlockDelta" in event:
                        d = event["contentBlockDelta"]
                        delta = d.get("delta", {})
                        if "text" in delta:
                            yield builder.text(delta["text"])
                        elif "toolUse" in delta:
                            chunk = builder.tool_args(
                                int(d.get("contentBlockIndex", 0)),
                                delta["toolUse"].get("input", ""),
                            )
                            if chunk is not None:
                                yield chunk
                    elif "messageStop" in event:
                        yield builder.finish(
                            map_stop_reason(event["messageStop"].get("stopReason"))
                        )
                    elif "metadata" in event:
                        usage = event["metadata"].get("usage", {})
                        yield builder.usage(
                            int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
                        )
                    elif any(k.endswith("Exception") for k in event):
                        name, detail = next(iter(event.items()))
                        raise ProviderUnavailableError(
                            f"bedrock stream {name}: {detail.get('message', '')}",
                            provider=self.kind,
                            deployment=deployment.id,
                        )
        except (ClientError, BotoCoreError) as exc:
            raise self._classify(exc, deployment) from exc

    async def embed(self, req: EmbeddingRequest, deployment: Deployment) -> EmbeddingResponse:
        raise ProviderBadRequestError(
            "bedrock embeddings are not supported in this phase",
            provider=self.kind,
            deployment=deployment.id,
        )

    async def aclose(self) -> None:
        return None
