import json

import pytest
import structlog

from switchboard.core.context import RequestContext, clear_request_context, set_request_context
from switchboard.core.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()


def test_json_logs_carry_request_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", json=True)
    set_request_context(RequestContext(request_id="req-1", tenant_id="tenant-a"))
    try:
        get_logger("t").info("hello", answer=42)
    finally:
        clear_request_context()
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "hello"
    assert record["answer"] == 42
    assert record["request_id"] == "req-1"
    assert record["tenant_id"] == "tenant-a"
    assert record["level"] == "info"


def test_console_logs_without_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="DEBUG", json=False)
    get_logger("t").debug("plain")
    out = capsys.readouterr().out
    assert "plain" in out
    assert "request_id" not in out


def test_level_filter(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="WARNING", json=True)
    get_logger("t").info("dropped")
    assert capsys.readouterr().out.strip() == ""
