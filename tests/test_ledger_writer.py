import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from switchboard.ledger.models import UsageEvent
from switchboard.ledger.writer import LedgerWriter


def _event(i: int) -> UsageEvent:
    return UsageEvent(
        ts=datetime.now(UTC),
        request_id=f"r{i}",
        tenant_id="t",
        api_key_id="k",
        requested_model="m",
        total_ms=1,
        status="ok",
    )


class CollectingSink:
    def __init__(self, fail_times: int = 0) -> None:
        self.batches: list[list[UsageEvent]] = []
        self.fail_times = fail_times

    async def write_batch(self, events: Sequence[UsageEvent]) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("db down")
        self.batches.append(list(events))


async def test_background_flush_batches_events() -> None:
    sink = CollectingSink()
    writer = LedgerWriter(sink, flush_interval_s=0.02, max_batch=10)
    await writer.start()
    for i in range(25):
        writer.emit(_event(i))
    await asyncio.sleep(0.3)
    await writer.stop()
    assert sum(len(b) for b in sink.batches) == 25
    assert max(len(b) for b in sink.batches) <= 10
    assert writer.written == 25


async def test_stop_drains_remaining() -> None:
    sink = CollectingSink()
    writer = LedgerWriter(sink, flush_interval_s=10)
    await writer.start()
    writer.emit(_event(1))
    await writer.stop()
    assert len(sink.batches) == 1
    await writer.stop()  # idempotent


async def test_sink_failure_is_logged_not_raised() -> None:
    sink = CollectingSink(fail_times=1)
    writer = LedgerWriter(sink, flush_interval_s=10)
    writer.emit(_event(1))
    assert await writer.flush() == 0
    writer.emit(_event(2))
    assert await writer.flush() == 1


async def test_queue_full_drops() -> None:
    sink = CollectingSink()
    writer = LedgerWriter(sink, flush_interval_s=10, max_queue=2)
    for i in range(5):
        writer.emit(_event(i))
    assert await writer.flush() == 2
