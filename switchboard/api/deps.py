"""FastAPI dependencies: application services and the calling principal."""

from typing import Annotated

from fastapi import Depends, Request

from switchboard.auth.models import Principal, Role
from switchboard.core.context import get_request_context
from switchboard.executor.executor import Executor
from switchboard.schemas.errors import AuthenticationError, PermissionDeniedError
from switchboard.storage.base import Storage


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def get_executor(request: Request) -> Executor:
    executor: Executor = request.app.state.executor
    return executor


async def get_principal(
    request: Request, storage: Annotated[Storage, Depends(get_storage)]
) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("missing bearer API key")
    principal = await storage.auth.resolve(token.strip())
    if principal is None:
        raise AuthenticationError("invalid or revoked API key")
    ctx = get_request_context()
    if ctx is not None:
        ctx.tenant_id = principal.tenant_id
        ctx.api_key_id = principal.api_key_id
    return principal


def require_role(*roles: Role):  # type: ignore[no-untyped-def]
    async def _check(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has_role(*roles):
            raise PermissionDeniedError(f"this endpoint requires one of roles: {', '.join(roles)}")
        return principal

    return _check


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
AdminPrincipal = Annotated[Principal, Depends(require_role("admin"))]
StorageDep = Annotated[Storage, Depends(get_storage)]
ExecutorDep = Annotated[Executor, Depends(get_executor)]
