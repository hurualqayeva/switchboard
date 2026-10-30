"""Structured logging setup. Every log line carries the request context when present."""

import logging
import sys

import structlog

from switchboard.core.context import get_request_context


def _add_request_context(
    _logger: logging.Logger, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    ctx = get_request_context()
    if ctx is not None:
        event_dict.setdefault("request_id", ctx.request_id)
        if ctx.tenant_id is not None:
            event_dict.setdefault("tenant_id", ctx.tenant_id)
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
