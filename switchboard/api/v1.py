"""OpenAI-compatible endpoints: /v1/chat/completions, /v1/embeddings, /v1/models."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from switchboard.api.deps import CurrentPrincipal, ExecutorDep
from switchboard.core.logging import get_logger
from switchboard.schemas.errors import SwitchboardError
from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelList,
    ModelObject,
)

router = APIRouter(prefix="/v1", tags=["openai"])
log = get_logger(__name__)

DEPLOYMENT_HEADER = "x-switchboard-deployment"


def _sse(chunk: ChatCompletionChunk) -> str:
    return f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


async def _sse_body(
    first: ChatCompletionChunk, rest: AsyncIterator[ChatCompletionChunk], *, include_usage: bool
) -> AsyncIterator[str]:
    try:
        if include_usage or first.choices:
            yield _sse(first)
        async for chunk in rest:
            if not include_usage and not chunk.choices and chunk.usage is not None:
                continue
            yield _sse(chunk)
    except SwitchboardError as exc:
        # Headers are already sent; surface the failure in-band the way OpenAI does.
        yield f"data: {json.dumps(exc.envelope())}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    req: ChatCompletionRequest, principal: CurrentPrincipal, executor: ExecutorDep, request: Request
) -> ChatCompletionResponse | StreamingResponse:
    if not req.stream:
        resp = await executor.chat(req, principal)
        request.state.deployment = resp.model
        return resp

    stream = executor.chat_stream(req, principal)
    # Pull the first chunk before committing to a 200 so provider errors keep their status.
    first = await anext(stream)
    include_usage = bool(req.stream_options and req.stream_options.include_usage)
    return StreamingResponse(
        _sse_body(first, stream, include_usage=include_usage),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
            DEPLOYMENT_HEADER: first.model,
        },
    )


@router.post("/embeddings", response_model=EmbeddingResponse)
async def embeddings(
    req: EmbeddingRequest, principal: CurrentPrincipal, executor: ExecutorDep
) -> EmbeddingResponse:
    return await executor.embed(req, principal)


@router.get("/models", response_model=ModelList)
async def list_models(principal: CurrentPrincipal, request: Request) -> ModelList:
    catalog = request.app.state.catalog
    return ModelList(
        data=[ModelObject(id=d.id, owned_by=d.provider) for d in catalog.list()],
    )
