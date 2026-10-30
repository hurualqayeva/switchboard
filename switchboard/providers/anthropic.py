"""Adapter for the Anthropic Messages API."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from switchboard.catalog.models import Deployment, ProviderKind
from switchboard.providers.base import classify_http_error, shared_http_client
from switchboard.providers.common import (
    ChunkBuilder,
    map_stop_reason,
    parse_tool_arguments,
    split_data_url,
    system_text,
)
from switchboard.providers.sse import iter_sse
from switchboard.schemas.errors import (
    ProviderBadRequestError,
    ProviderError,
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

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


# -- request translation -----------------------------------------------------------------


def _user_content(msg: ChatMessage) -> str | list[dict[str, Any]]:
    if isinstance(msg.content, str) or msg.content is None:
        return msg.content or ""
    blocks: list[dict[str, Any]] = []
    for part in msg.content:
        if part.type == "text":
            blocks.append({"type": "text", "text": part.text or ""})
        elif part.type == "image_url" and part.image_url is not None:
            data = split_data_url(part.image_url.url)
            source = (
                {"type": "base64", "media_type": data[0], "data": data[1]}
                if data
                else {"type": "url", "url": part.image_url.url}
            )
            blocks.append({"type": "image", "source": source})
    return blocks


def _assistant_content(msg: ChatMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if text := msg.text():
        blocks.append({"type": "text", "text": text})
    for tc in msg.tool_calls or []:
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": parse_tool_arguments(tc.function.arguments),
            }
        )
    return blocks


def to_anthropic_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role in ("system", "developer"):
            continue
        if msg.role == "user":
            out.append({"role": "user", "content": _user_content(msg)})
        elif msg.role == "assistant":
            blocks = _assistant_content(msg)
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif msg.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id or "",
                "content": msg.text(),
            }
            last = out[-1] if out else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last["content"], list)
                and last["content"]
                and last["content"][0].get("type") == "tool_result"
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def to_anthropic_request(req: ChatCompletionRequest, deployment: Deployment) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": deployment.model,
        "messages": to_anthropic_messages(req.messages),
        "max_tokens": req.effective_max_tokens
        or min(deployment.max_output_tokens, DEFAULT_MAX_TOKENS),
    }
    if system := system_text(req.messages):
        body["system"] = system
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.top_p is not None:
        body["top_p"] = req.top_p
    if req.stop_sequences:
        body["stop_sequences"] = req.stop_sequences
    if req.user:
        body["metadata"] = {"user_id": req.user}
    if req.tools:
        body["tools"] = [
            {
                "name": t.function.name,
                "description": t.function.description or "",
                "input_schema": t.function.parameters,
            }
            for t in req.tools
        ]
        choice = req.tool_choice
        if isinstance(choice, NamedToolChoice):
            body["tool_choice"] = {"type": "tool", "name": choice.function["name"]}
        elif choice == "required":
            body["tool_choice"] = {"type": "any"}
        elif choice == "none":
            body["tool_choice"] = {"type": "none"}
        elif choice == "auto":
            body["tool_choice"] = {"type": "auto"}
    return body


# -- response translation ----------------------------------------------------------------


def from_anthropic_response(data: dict[str, Any], deployment: Deployment) -> ChatCompletionResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block["id"],
                    function=FunctionCall(
                        name=block["name"], arguments=json.dumps(block.get("input", {}))
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
        id=data.get("id", new_completion_id()),
        model=deployment.id,
        choices=[
            Choice(index=0, message=message, finish_reason=map_stop_reason(data.get("stop_reason")))
        ],
        usage=Usage.of(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
    )


# -- adapter -----------------------------------------------------------------------------


class AnthropicAdapter:
    kind: ProviderKind = "anthropic"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or shared_http_client(timeout_s=120.0)

    def _headers(self, deployment: Deployment) -> dict[str, str]:
        key = deployment.api_key()
        if not key:
            raise ProviderError(
                f"no API key for deployment '{deployment.id}' (env {deployment.api_key_env})",
                provider=self.kind,
                deployment=deployment.id,
                code="provider_auth",
            )
        return {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    @staticmethod
    def _url(deployment: Deployment) -> str:
        base = (deployment.base_url or "https://api.anthropic.com").rstrip("/")
        return f"{base}/v1/messages"

    def _timeout(self, deployment: Deployment) -> httpx.Timeout:
        return httpx.Timeout(deployment.timeout_s, connect=5.0)

    async def complete(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> ChatCompletionResponse:
        body = to_anthropic_request(req, deployment)
        try:
            resp = await self._client.post(
                self._url(deployment),
                json=body,
                headers=self._headers(deployment),
                timeout=self._timeout(deployment),
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"timeout after {deployment.timeout_s}s",
                provider=self.kind,
                deployment=deployment.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"connection failed: {exc}", provider=self.kind, deployment=deployment.id
            ) from exc
        if resp.status_code >= 400:
            raise classify_http_error(
                resp.status_code, resp.text, provider=self.kind, deployment=deployment.id
            )
        return from_anthropic_response(resp.json(), deployment)

    async def stream(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> AsyncIterator[ChatCompletionChunk]:
        body = to_anthropic_request(req, deployment)
        body["stream"] = True
        builder = ChunkBuilder(new_completion_id(), deployment.id)
        prompt_tokens = 0
        completion_tokens = 0
        try:
            async with self._client.stream(
                "POST",
                self._url(deployment),
                json=body,
                headers=self._headers(deployment),
                timeout=self._timeout(deployment),
            ) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise classify_http_error(
                        resp.status_code, text, provider=self.kind, deployment=deployment.id
                    )
                async for event in iter_sse(resp.aiter_lines()):
                    data = json.loads(event.data)
                    kind = data.get("type")
                    if kind == "message_start":
                        msg = data.get("message", {})
                        builder.id = msg.get("id", builder.id)
                        prompt_tokens = int(msg.get("usage", {}).get("input_tokens", 0))
                    elif kind == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            yield builder.tool_start(int(data["index"]), block["id"], block["name"])
                    elif kind == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield builder.text(delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            chunk = builder.tool_args(
                                int(data["index"]), delta.get("partial_json", "")
                            )
                            if chunk is not None:
                                yield chunk
                    elif kind == "message_delta":
                        completion_tokens = int(
                            data.get("usage", {}).get("output_tokens", completion_tokens)
                        )
                        yield builder.finish(
                            map_stop_reason(data.get("delta", {}).get("stop_reason"))
                        )
                    elif kind == "error":
                        err = data.get("error", {})
                        raise ProviderUnavailableError(
                            f"anthropic stream error: {err.get('message', err)}",
                            provider=self.kind,
                            deployment=deployment.id,
                        )
                    elif kind == "message_stop":
                        yield builder.usage(prompt_tokens, completion_tokens)
                        return
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"timeout after {deployment.timeout_s}s",
                provider=self.kind,
                deployment=deployment.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"connection failed: {exc}", provider=self.kind, deployment=deployment.id
            ) from exc

    async def embed(self, req: EmbeddingRequest, deployment: Deployment) -> EmbeddingResponse:
        raise ProviderBadRequestError(
            "anthropic deployments do not serve embeddings",
            provider=self.kind,
            deployment=deployment.id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
