from switchboard.core.context import (
    RequestContext,
    clear_request_context,
    get_request_context,
    set_request_context,
)


def test_context_roundtrip() -> None:
    assert get_request_context() is None
    ctx = RequestContext(tenant_id="t1")
    set_request_context(ctx)
    assert get_request_context() is ctx
    clear_request_context()
    assert get_request_context() is None


def test_request_id_is_unique() -> None:
    assert RequestContext().request_id != RequestContext().request_id
