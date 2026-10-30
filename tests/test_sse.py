from collections.abc import AsyncIterator

from switchboard.providers.sse import iter_sse


async def _lines(text: str) -> AsyncIterator[str]:
    for line in text.split("\n"):
        yield line + "\n"


async def test_parses_events_and_multiline_data() -> None:
    raw = ': keepalive\nevent: ping\ndata: {"a":1}\n\ndata: one\ndata: two\n\ndata: last'
    events = [e async for e in iter_sse(_lines(raw))]
    assert [(e.event, e.data) for e in events] == [
        ("ping", '{"a":1}'),
        (None, "one\ntwo"),
        (None, "last"),
    ]


async def test_handles_crlf_and_no_space_after_colon() -> None:
    raw = "data:x\r\n\r\n"
    events = [e async for e in iter_sse(_lines(raw))]
    assert events[0].data == "x"
