"""Minimal server-sent events parser for provider streams."""

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(slots=True)
class SseEvent:
    event: str | None
    data: str


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Yield one event per blank-line-terminated block, joining multi-line data fields."""
    event: str | None = None
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if data:
                yield SseEvent(event=event, data="\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            continue  # comment / keepalive
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield SseEvent(event=event, data="\n".join(data))
