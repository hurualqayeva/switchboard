"""Provider adapter protocol and registry.

An adapter translates between the OpenAI-shaped internal schema and one provider's wire
format. Adapters know nothing about tenants, budgets or routing: they complete, stream,
embed and count tokens, and raise classified ProviderError subclasses on failure.
"""

from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from switchboard.catalog.models import Deployment, ProviderKind
from switchboard.schemas.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
)


class ProviderAdapter(Protocol):
    kind: ProviderKind

    async def complete(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> ChatCompletionResponse: ...

    def stream(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> AsyncIterator[ChatCompletionChunk]: ...

    async def embed(self, req: EmbeddingRequest, deployment: Deployment) -> EmbeddingResponse: ...

    async def aclose(self) -> None: ...


def classify_http_error(status: int, body: str, *, provider: str, deployment: str) -> ProviderError:
    """Map an upstream HTTP status to the ProviderError subclass the executor expects."""
    detail = body[:300].strip() or f"HTTP {status}"
    cls: type[ProviderError]
    if status in (401, 403):
        cls, text = ProviderAuthError, f"{provider} rejected credentials: {detail}"
    elif status == 429:
        cls, text = ProviderRateLimitedError, f"{provider} rate limited: {detail}"
    elif status in (408, 409, 500, 502, 503, 504, 529):
        cls, text = ProviderUnavailableError, f"{provider} unavailable: {detail}"
    elif 400 <= status < 500:
        cls, text = ProviderBadRequestError, f"{provider} rejected request: {detail}"
    else:
        cls, text = ProviderError, f"{provider} error: {detail}"
    return cls(text, provider=provider, deployment=deployment, upstream_status=status)


def shared_http_client(timeout_s: float) -> httpx.AsyncClient:
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=64)
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0), limits=limits)


class ProviderRegistry:
    def __init__(self, adapters: dict[ProviderKind, ProviderAdapter]) -> None:
        self._adapters = adapters

    def for_deployment(self, deployment: Deployment) -> ProviderAdapter:
        try:
            return self._adapters[deployment.provider]
        except KeyError as exc:
            msg = f"no adapter registered for provider '{deployment.provider}'"
            raise LookupError(msg) from exc

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            await adapter.aclose()


def build_default_registry() -> ProviderRegistry:
    from switchboard.providers.anthropic import AnthropicAdapter
    from switchboard.providers.bedrock import BedrockAdapter
    from switchboard.providers.openai_compat import OpenAICompatAdapter

    return ProviderRegistry(
        {
            "openai_compat": OpenAICompatAdapter(),
            "anthropic": AnthropicAdapter(),
            "bedrock": BedrockAdapter(),
        }
    )
