"""Adapter for any OpenAI-compatible HTTP API: OpenAI, vLLM, llama.cpp."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from switchboard.catalog.models import Deployment, ProviderKind
from switchboard.providers.base import classify_http_error, shared_http_client
from switchboard.providers.sse import iter_sse
from switchboard.schemas.errors import ProviderError, ProviderTimeoutError, ProviderUnavailableError
from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
)


class OpenAICompatAdapter:
    kind: ProviderKind = "openai_compat"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or shared_http_client(timeout_s=120.0)

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def _headers(deployment: Deployment) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if key := deployment.api_key():
            headers["authorization"] = f"Bearer {key}"
        return headers

    @staticmethod
    def _url(deployment: Deployment, path: str) -> str:
        base = (deployment.base_url or "").rstrip("/")
        if not base:
            msg = f"deployment '{deployment.id}' has no base_url"
            raise ProviderError(msg, provider="openai_compat", deployment=deployment.id)
        return f"{base}{path}"

    @staticmethod
    def _body(
        req: ChatCompletionRequest, deployment: Deployment, *, stream: bool
    ) -> dict[str, Any]:
        body = req.model_dump(exclude_none=True, exclude={"metadata"})
        body["model"] = deployment.model
        body["stream"] = stream
        if stream and deployment.capabilities.stream_usage:
            body["stream_options"] = {"include_usage": True}
        elif not stream:
            body.pop("stream_options", None)
        return body

    def _timeout(self, deployment: Deployment) -> httpx.Timeout:
        return httpx.Timeout(deployment.timeout_s, connect=5.0)

    async def _post(self, url: str, body: dict[str, Any], deployment: Deployment) -> httpx.Response:
        try:
            resp = await self._client.post(
                url, json=body, headers=self._headers(deployment), timeout=self._timeout(deployment)
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
        return resp

    # -- protocol ------------------------------------------------------------------------

    async def complete(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> ChatCompletionResponse:
        resp = await self._post(
            self._url(deployment, "/chat/completions"),
            self._body(req, deployment, stream=False),
            deployment,
        )
        out = ChatCompletionResponse.model_validate(resp.json())
        out.model = deployment.id
        return out

    async def stream(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = self._url(deployment, "/chat/completions")
        body = self._body(req, deployment, stream=True)
        try:
            async with self._client.stream(
                "POST",
                url,
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
                    if event.data.strip() == "[DONE]":
                        return
                    chunk = ChatCompletionChunk.model_validate(json.loads(event.data))
                    chunk.model = deployment.id
                    yield chunk
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
        body = req.model_dump(exclude_none=True)
        body["model"] = deployment.model
        resp = await self._post(self._url(deployment, "/embeddings"), body, deployment)
        out = EmbeddingResponse.model_validate(resp.json())
        out.model = deployment.id
        return out

    async def aclose(self) -> None:
        await self._client.aclose()
