"""Batched, non-blocking ledger writer.

Request handlers call ``emit`` which only enqueues. A background task flushes the queue to
the sink every ``flush_interval_s`` or when ``max_batch`` events are waiting, whichever is
first. ``stop`` drains what is left so a graceful shutdown loses nothing; a crash loses at
most one flush interval of events (ADR-0003).
"""

import asyncio
import contextlib

from switchboard.core.logging import get_logger
from switchboard.ledger.models import UsageEvent
from switchboard.storage.base import LedgerSink

log = get_logger(__name__)


class LedgerWriter:
    def __init__(
        self,
        sink: LedgerSink,
        *,
        flush_interval_s: float = 1.0,
        max_batch: int = 200,
        max_queue: int = 50_000,
    ) -> None:
        self._sink = sink
        self._flush_interval = flush_interval_s
        self._max_batch = max_batch
        self._queue: asyncio.Queue[UsageEvent] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0
        self.written = 0

    def emit(self, event: UsageEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            log.warning("ledger.dropped", dropped=self._dropped, request_id=event.request_id)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="ledger-writer")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        await self.flush()

    async def flush(self) -> int:
        batch: list[UsageEvent] = []
        while not self._queue.empty() and len(batch) < self._max_batch:
            batch.append(self._queue.get_nowait())
        if not batch:
            return 0
        try:
            await self._sink.write_batch(batch)
        except Exception:
            log.exception("ledger.write_failed", batch=len(batch))
            return 0
        self.written += len(batch)
        return len(batch)

    async def _run(self) -> None:
        while True:
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
            except TimeoutError:
                continue
            # Put the first event back at the front of the batch and drain the rest.
            batch = [first]
            while not self._queue.empty() and len(batch) < self._max_batch:
                batch.append(self._queue.get_nowait())
            if len(batch) < self._max_batch:
                await asyncio.sleep(min(self._flush_interval, 0.05))
                while not self._queue.empty() and len(batch) < self._max_batch:
                    batch.append(self._queue.get_nowait())
            try:
                await self._sink.write_batch(batch)
                self.written += len(batch)
            except Exception:
                log.exception("ledger.write_failed", batch=len(batch))
