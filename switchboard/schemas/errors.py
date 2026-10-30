"""Error envelope and exception hierarchy.

Every error leaves the gateway in the OpenAI error shape so existing SDKs can parse it.
Provider failures are classified so the executor (phase 2) can decide whether to fall back.
"""

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    message: str
    type: str
    param: str | None = None
    code: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class SwitchboardError(Exception):
    status_code: int = 500
    error_type: str = "server_error"
    code: str | None = None
    retryable: bool = False

    def __init__(self, message: str, *, param: str | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.param = param
        if code is not None:
            self.code = code

    def envelope(self) -> dict[str, Any]:
        return ErrorEnvelope(
            error=ErrorBody(
                message=self.message, type=self.error_type, param=self.param, code=self.code
            )
        ).model_dump()


class InvalidRequestError(SwitchboardError):
    status_code = 400
    error_type = "invalid_request_error"


class AuthenticationError(SwitchboardError):
    status_code = 401
    error_type = "authentication_error"
    code = "invalid_api_key"


class PermissionDeniedError(SwitchboardError):
    status_code = 403
    error_type = "permission_error"
    code = "insufficient_role"


class ModelNotFoundError(SwitchboardError):
    status_code = 404
    error_type = "invalid_request_error"
    code = "model_not_found"


class ProviderError(SwitchboardError):
    """Base for failures that happened while talking to an upstream provider."""

    status_code = 502
    error_type = "provider_error"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        deployment: str,
        upstream_status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.provider = provider
        self.deployment = deployment
        self.upstream_status = upstream_status


class ProviderBadRequestError(ProviderError):
    """The provider rejected the request as malformed. Retrying elsewhere will not help."""

    status_code = 400
    error_type = "invalid_request_error"
    retryable = False


class ProviderAuthError(ProviderError):
    """Our credentials for the provider are wrong. Not retryable on this deployment."""

    status_code = 502
    code = "provider_auth"
    retryable = False


class ProviderRateLimitedError(ProviderError):
    status_code = 429
    error_type = "rate_limit_error"
    code = "provider_rate_limited"


class ProviderUnavailableError(ProviderError):
    status_code = 503
    code = "provider_unavailable"


class ProviderTimeoutError(ProviderError):
    status_code = 504
    code = "provider_timeout"
