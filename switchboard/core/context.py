"""Per-request context propagated through logs, spans and ledger rows."""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(slots=True)
class RequestContext:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str | None = None
    api_key_id: str | None = None
    experiment_id: str | None = None


_request_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def get_request_context() -> RequestContext | None:
    return _request_context.get()


def set_request_context(ctx: RequestContext) -> None:
    _request_context.set(ctx)


def clear_request_context() -> None:
    _request_context.set(None)
