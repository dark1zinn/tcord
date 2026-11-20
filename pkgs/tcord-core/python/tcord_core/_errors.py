"""Exceptions raised by the native engine. Each maps one Rust ``EngineError`` variant."""


class TcordError(Exception):
    """Base class for every engine failure."""


class NetworkError(TcordError):
    """Discord could not be reached or the connection dropped."""


class AuthenticationError(TcordError):
    """The session is not authenticated (missing, expired or revoked token)."""


class PermissionDeniedError(TcordError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class NotFoundError(TcordError):
    """The requested Discord resource does not exist."""


class RateLimitedError(TcordError):
    def __init__(self, message: str, retry_after: float, is_global: bool) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.is_global = is_global


class CaptchaRequiredError(TcordError):
    def __init__(self, message: str, service: str | None, sitekey: str | None) -> None:
        super().__init__(message)
        self.service = service
        self.sitekey = sitekey


class InvalidInputError(TcordError, ValueError):
    """The arguments were rejected before any request was made."""


class HttpError(TcordError):
    def __init__(self, message: str, status: int, code: int | None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class ProtocolError(TcordError):
    """Discord answered with something the engine could not understand."""


class CredentialStoreError(TcordError):
    """The operating-system credential store failed."""


class CancelledError(TcordError):
    """The operation was cancelled by logout or session close."""
