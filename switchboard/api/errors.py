"""Exception handlers that keep every error in the OpenAI envelope shape."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from switchboard.core.logging import get_logger
from switchboard.schemas.errors import ErrorBody, ErrorEnvelope, SwitchboardError

log = get_logger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SwitchboardError)
    async def _switchboard_error(_: Request, exc: SwitchboardError) -> JSONResponse:
        if exc.status_code >= 500:
            log.warning("request.failed", code=exc.code, message=exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.envelope())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body") or None
        body = ErrorEnvelope(
            error=ErrorBody(
                message=f"{loc}: {first.get('msg', 'invalid request')}"
                if loc
                else "invalid request",
                type="invalid_request_error",
                param=loc,
                code="invalid_request",
            )
        )
        return JSONResponse(status_code=400, content=body.model_dump())

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("request.unhandled", error=str(exc))
        body = ErrorEnvelope(
            error=ErrorBody(message="internal error", type="server_error", code="internal")
        )
        return JSONResponse(status_code=500, content=body.model_dump())
