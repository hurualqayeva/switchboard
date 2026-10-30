"""Helpers shared by the block-based providers (Anthropic Messages API, Bedrock Converse)."""

import base64
import json
import time
from typing import Any

from switchboard.schemas.openai import (
    ChatCompletionChunk,
    ChatMessage,
    ChunkChoice,
    DeltaMessage,
    FinishReason,
    FunctionCallDelta,
    ToolCallDelta,
    Usage,
)

STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "content_filtered": "content_filter",
    "refusal": "content_filter",
}


def map_stop_reason(reason: str | None) -> FinishReason | None:
    if reason is None:
        return None
    return STOP_REASONS.get(reason, "stop")


def parse_tool_arguments(arguments: str | None) -> dict[str, Any]:
    """OpenAI tool arguments are a JSON string; block providers want an object."""
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"_raw": arguments}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def split_data_url(url: str) -> tuple[str, str] | None:
    """Return (media_type, base64_payload) for a data: URL, else None."""
    if not url.startswith("data:"):
        return None
    header, _, payload = url.partition(",")
    media_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    if ";base64" not in header:
        payload = base64.b64encode(payload.encode()).decode()
    return media_type, payload


def system_text(messages: list[ChatMessage]) -> str | None:
    parts = [m.text() for m in messages if m.role in ("system", "developer") and m.text()]
    return "\n\n".join(parts) if parts else None


class ChunkBuilder:
    """Builds OpenAI-shaped stream chunks while tracking tool-call indexes.

    Block providers number content blocks; OpenAI numbers tool calls. This maps the former
    to the latter and remembers which block index is a tool so argument deltas land on the
    right tool call.
    """

    def __init__(self, completion_id: str, model: str) -> None:
        self.id = completion_id
        self.model = model
        self.created = int(time.time())
        self._tool_index_by_block: dict[int, int] = {}
        self._sent_role = False

    def _chunk(
        self, delta: DeltaMessage, finish: FinishReason | None = None
    ) -> ChatCompletionChunk:
        if not self._sent_role:
            delta.role = "assistant"
            self._sent_role = True
        return ChatCompletionChunk(
            id=self.id,
            created=self.created,
            model=self.model,
            choices=[ChunkChoice(index=0, delta=delta, finish_reason=finish)],
        )

    def text(self, text: str) -> ChatCompletionChunk:
        return self._chunk(DeltaMessage(content=text))

    def tool_start(self, block_index: int, tool_id: str, name: str) -> ChatCompletionChunk:
        tool_index = len(self._tool_index_by_block)
        self._tool_index_by_block[block_index] = tool_index
        return self._chunk(
            DeltaMessage(
                tool_calls=[
                    ToolCallDelta(
                        index=tool_index,
                        id=tool_id,
                        type="function",
                        function=FunctionCallDelta(name=name, arguments=""),
                    )
                ]
            )
        )

    def tool_args(self, block_index: int, partial_json: str) -> ChatCompletionChunk | None:
        tool_index = self._tool_index_by_block.get(block_index)
        if tool_index is None or partial_json == "":
            return None
        return self._chunk(
            DeltaMessage(
                tool_calls=[
                    ToolCallDelta(
                        index=tool_index, function=FunctionCallDelta(arguments=partial_json)
                    )
                ]
            )
        )

    def finish(self, reason: FinishReason | None) -> ChatCompletionChunk:
        return self._chunk(DeltaMessage(), finish=reason or "stop")

    def usage(self, prompt: int, completion: int) -> ChatCompletionChunk:
        return ChatCompletionChunk(
            id=self.id,
            created=self.created,
            model=self.model,
            choices=[],
            usage=Usage.of(prompt, completion),
        )
