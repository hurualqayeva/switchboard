"""Test doubles shared across test modules."""

from collections.abc import AsyncIterator

from switchboard.catalog.models import Deployment, ProviderKind
from switchboard.providers.base import ProviderRegistry
from switchboard.schemas.errors import SwitchboardError
from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    DeltaMessage,
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUsage,
    Usage,
)


class StubAdapter:
    """Returns canned answers, records calls, and can be told to fail."""

    kind: ProviderKind = "openai_compat"

    def __init__(
        self, *, reply: str = "hello from stub", fail: SwitchboardError | None = None
    ) -> None:
        self.reply = reply
        self.fail = fail
        self.fail_after_chunks: int | None = None
        self.calls: list[tuple[str, ChatCompletionRequest | EmbeddingRequest, Deployment]] = []
        self.report_usage = True

    async def complete(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> ChatCompletionResponse:
        self.calls.append(("complete", req, deployment))
        if self.fail:
            raise self.fail
        return ChatCompletionResponse(
            model=deployment.id,
            choices=[
                Choice(
                    message=ChatMessage(role="assistant", content=self.reply), finish_reason="stop"
                )
            ],
            usage=Usage.of(10, 5) if self.report_usage else Usage(),
        )

    async def stream(
        self, req: ChatCompletionRequest, deployment: Deployment
    ) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append(("stream", req, deployment))
        if self.fail and self.fail_after_chunks is None:
            raise self.fail
        words = self.reply.split(" ")
        for i, word in enumerate(words):
            if self.fail and self.fail_after_chunks is not None and i >= self.fail_after_chunks:
                raise self.fail
            delta = DeltaMessage(
                role="assistant" if i == 0 else None,
                content=word + (" " if i < len(words) - 1 else ""),
            )
            yield ChatCompletionChunk(
                id="chatcmpl-stub", model=deployment.id, choices=[ChunkChoice(delta=delta)]
            )
        yield ChatCompletionChunk(
            id="chatcmpl-stub",
            model=deployment.id,
            choices=[ChunkChoice(delta=DeltaMessage(), finish_reason="stop")],
        )
        if self.report_usage:
            yield ChatCompletionChunk(
                id="chatcmpl-stub", model=deployment.id, choices=[], usage=Usage.of(10, 5)
            )

    async def embed(self, req: EmbeddingRequest, deployment: Deployment) -> EmbeddingResponse:
        self.calls.append(("embed", req, deployment))
        if self.fail:
            raise self.fail
        return EmbeddingResponse(
            model=deployment.id,
            data=[
                EmbeddingData(index=i, embedding=[0.1, 0.2, 0.3]) for i, _ in enumerate(req.inputs)
            ],
            usage=EmbeddingUsage(prompt_tokens=7, total_tokens=7),
        )

    async def aclose(self) -> None:
        return None


def stub_registry(adapter: StubAdapter) -> ProviderRegistry:
    return ProviderRegistry({"openai_compat": adapter, "anthropic": adapter, "bedrock": adapter})


async def drain(it: AsyncIterator[ChatCompletionChunk]) -> list[ChatCompletionChunk]:
    """Consume a stream fully; used inside pytest.raises blocks."""
    return [c async for c in it]
