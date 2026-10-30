"""Executes a request against a deployment and records the ledger event.

Phase 1 resolves the deployment by explicit model id. Phase 2 inserts the router, fallback
chain and circuit breakers between ``resolve`` and the adapter call; the ledger and timing
code here does not change.
"""

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from switchboard.auth.models import Principal
from switchboard.catalog.models import Catalog, Deployment
from switchboard.core.context import get_request_context
from switchboard.core.logging import get_logger
from switchboard.core.tokens import count_text_tokens, estimate_prompt_tokens
from switchboard.ledger.models import UsageEvent
from switchboard.ledger.writer import LedgerWriter
from switchboard.providers.base import ProviderRegistry
from switchboard.schemas.errors import InvalidRequestError, ModelNotFoundError, SwitchboardError
from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    Usage,
)

log = get_logger(__name__)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _request_id() -> str:
    ctx = get_request_context()
    return ctx.request_id if ctx else "no-request-id"


class Executor:
    def __init__(self, catalog: Catalog, registry: ProviderRegistry, ledger: LedgerWriter) -> None:
        self._catalog = catalog
        self._registry = registry
        self._ledger = ledger

    # -- resolution ----------------------------------------------------------------------

    def resolve(self, model_id: str, *, need: str = "chat") -> Deployment:
        dep = self._catalog.get(model_id)
        if dep is None:
            raise ModelNotFoundError(f"model '{model_id}' does not exist", param="model")
        caps = dep.capabilities
        if need == "chat" and not caps.chat:
            raise InvalidRequestError(
                f"model '{model_id}' does not serve chat completions", param="model"
            )
        if need == "embeddings" and not caps.embeddings:
            raise InvalidRequestError(
                f"model '{model_id}' does not serve embeddings", param="model"
            )
        return dep

    def _check_request(self, req: ChatCompletionRequest, dep: Deployment) -> None:
        if req.n != 1:
            raise InvalidRequestError("only n=1 is supported", param="n")
        if req.tools and not dep.capabilities.tools:
            raise InvalidRequestError(f"model '{dep.id}' does not support tools", param="tools")
        if req.stream and not dep.capabilities.streaming:
            raise InvalidRequestError(
                f"model '{dep.id}' does not support streaming", param="stream"
            )

    # -- ledger --------------------------------------------------------------------------

    def _event(
        self,
        principal: Principal,
        requested_model: str,
        dep: Deployment | None,
        *,
        start: float,
        usage: Usage | None,
        stream: bool,
        ttft_ms: int | None = None,
        error: SwitchboardError | None = None,
        cancelled: bool = False,
    ) -> UsageEvent:
        prompt = usage.prompt_tokens if usage else 0
        completion = usage.completion_tokens if usage else 0
        cost = dep.pricing.cost(prompt, completion) if dep else Decimal(0)
        status = "error" if error else ("cancelled" if cancelled else "ok")
        return UsageEvent(
            ts=datetime.now(UTC),
            request_id=_request_id(),
            tenant_id=principal.tenant_id,
            api_key_id=principal.api_key_id,
            requested_model=requested_model,
            deployment_id=dep.id if dep else None,
            provider=dep.provider if dep else None,
            route_mode="explicit",
            route_reason="explicit model id",
            stream=stream,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cost_usd=cost,
            ttft_ms=ttft_ms,
            total_ms=_ms(start),
            status=status,
            error_code=error.code or error.error_type if error else None,
        )

    # -- chat ----------------------------------------------------------------------------

    async def chat(
        self, req: ChatCompletionRequest, principal: Principal
    ) -> ChatCompletionResponse:
        dep = self.resolve(req.model)
        self._check_request(req, dep)
        adapter = self._registry.for_deployment(dep)
        start = time.perf_counter()
        try:
            resp = await adapter.complete(req, dep)
        except SwitchboardError as exc:
            self._ledger.emit(
                self._event(
                    principal, req.model, dep, start=start, usage=None, stream=False, error=exc
                )
            )
            raise
        if resp.usage.total_tokens == 0:
            resp.usage = Usage.of(
                estimate_prompt_tokens(req.messages),
                sum(count_text_tokens(c.message.text()) for c in resp.choices),
            )
        self._ledger.emit(
            self._event(
                principal,
                req.model,
                dep,
                start=start,
                usage=resp.usage,
                stream=False,
                ttft_ms=_ms(start),
            )
        )
        return resp

    async def chat_stream(
        self, req: ChatCompletionRequest, principal: Principal
    ) -> AsyncIterator[ChatCompletionChunk]:
        dep = self.resolve(req.model)
        self._check_request(req, dep)
        adapter = self._registry.for_deployment(dep)
        start = time.perf_counter()
        ttft: int | None = None
        usage: Usage | None = None
        completion_text: list[str] = []
        finished = False
        try:
            async for chunk in adapter.stream(req, dep):
                if ttft is None:
                    ttft = _ms(start)
                if chunk.usage is not None:
                    usage = chunk.usage
                for choice in chunk.choices:
                    if choice.delta.content:
                        completion_text.append(choice.delta.content)
                yield chunk
            finished = True
        except SwitchboardError as exc:
            self._ledger.emit(
                self._event(
                    principal,
                    req.model,
                    dep,
                    start=start,
                    usage=usage,
                    stream=True,
                    ttft_ms=ttft,
                    error=exc,
                )
            )
            raise
        finally:
            if usage is None or usage.total_tokens == 0:
                usage = Usage.of(
                    estimate_prompt_tokens(req.messages),
                    count_text_tokens("".join(completion_text)),
                )
            if finished:
                self._ledger.emit(
                    self._event(
                        principal,
                        req.model,
                        dep,
                        start=start,
                        usage=usage,
                        stream=True,
                        ttft_ms=ttft,
                    )
                )
            elif not finished and ttft is not None:
                # Client disconnected or the generator was closed mid-stream.
                self._ledger.emit(
                    self._event(
                        principal,
                        req.model,
                        dep,
                        start=start,
                        usage=usage,
                        stream=True,
                        ttft_ms=ttft,
                        cancelled=True,
                    )
                )

    # -- embeddings ----------------------------------------------------------------------

    async def embed(self, req: EmbeddingRequest, principal: Principal) -> EmbeddingResponse:
        dep = self.resolve(req.model, need="embeddings")
        adapter = self._registry.for_deployment(dep)
        start = time.perf_counter()
        try:
            resp = await adapter.embed(req, dep)
        except SwitchboardError as exc:
            self._ledger.emit(
                self._event(
                    principal, req.model, dep, start=start, usage=None, stream=False, error=exc
                )
            )
            raise
        usage = Usage.of(resp.usage.prompt_tokens, 0)
        self._ledger.emit(
            self._event(
                principal,
                req.model,
                dep,
                start=start,
                usage=usage,
                stream=False,
                ttft_ms=_ms(start),
            )
        )
        return resp
