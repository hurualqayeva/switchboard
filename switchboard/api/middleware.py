"""Request context middleware: assigns a request id and exposes it on the response."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from switchboard.core.context import RequestContext, clear_request_context, set_request_context

REQUEST_ID_HEADER = "x-request-id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        ctx = RequestContext(request_id=incoming) if incoming else RequestContext()
        set_request_context(ctx)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers[REQUEST_ID_HEADER] = ctx.request_id
        return response
