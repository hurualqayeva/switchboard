"""OpenAI-compatible wire schemas.

These are the canonical request and response types inside Switchboard as well as on the
wire: adapters translate provider formats to and from these models. Only the subset of
the OpenAI schema that the platform understands is modelled; unknown request fields are
ignored rather than rejected so existing clients keep working.
"""

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "developer", "user", "assistant", "tool"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter"]


class ImageUrl(BaseModel):
    url: str
    detail: Literal["auto", "low", "high"] = "auto"


class ContentPart(BaseModel):
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: ImageUrl | None = None


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Role
    content: str | list[ContentPart] | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def text(self) -> str:
        """Flatten content to plain text (image parts are dropped)."""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return "".join(p.text or "" for p in self.content if p.type == "text")


class FunctionDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class Tool(BaseModel):
    type: Literal["function"] = "function"
    function: FunctionDefinition


class NamedToolChoice(BaseModel):
    type: Literal["function"] = "function"
    function: dict[str, str]


ToolChoice = Literal["none", "auto", "required"] | NamedToolChoice


class StreamOptions(BaseModel):
    include_usage: bool = True


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text", "json_object", "json_schema"] = "text"


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    n: int = 1
    stream: bool = False
    stream_options: StreamOptions | None = None
    stop: str | list[str] | None = None
    tools: list[Tool] | None = None
    tool_choice: ToolChoice | None = None
    response_format: ResponseFormat | None = None
    seed: int | None = None
    user: str | None = None
    metadata: dict[str, str] | None = None

    @property
    def effective_max_tokens(self) -> int | None:
        return self.max_completion_tokens or self.max_tokens

    @property
    def stop_sequences(self) -> list[str]:
        if self.stop is None:
            return []
        return [self.stop] if isinstance(self.stop, str) else list(self.stop)


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def of(cls, prompt: int, completion: int) -> "Usage":
        return cls(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        )


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: FinishReason | None = None


def new_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=new_completion_id)
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)
    system_fingerprint: str | None = None


class FunctionCallDelta(BaseModel):
    name: str | None = None
    arguments: str | None = None


class ToolCallDelta(BaseModel):
    index: int
    id: str | None = None
    type: Literal["function"] | None = None
    function: FunctionCallDelta | None = None


class DeltaMessage(BaseModel):
    role: Role | None = None
    content: str | None = None
    tool_calls: list[ToolCallDelta] | None = None


class ChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage = Field(default_factory=DeltaMessage)
    finish_reason: FinishReason | None = None


class ChatCompletionChunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChunkChoice] = Field(default_factory=list)
    usage: Usage | None = None
    system_fingerprint: str | None = None


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    input: str | list[str]
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: int | None = None
    user: str | None = None

    @property
    def inputs(self) -> list[str]:
        return [self.input] if isinstance(self.input, str) else list(self.input)


class EmbeddingData(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage = Field(default_factory=EmbeddingUsage)


class ModelObject(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "switchboard"


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelObject]
