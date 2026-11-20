"""Engine exceptions, re-exported so the UI depends only on ``client``."""

from tcord_core import (
    AuthenticationError,
    CancelledError,
    CaptchaRequiredError,
    CredentialStoreError,
    HttpError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitedError,
    TcordError,
)

__all__ = [
    "AuthenticationError",
    "CancelledError",
    "CaptchaRequiredError",
    "CredentialStoreError",
    "HttpError",
    "InvalidInputError",
    "NetworkError",
    "NotFoundError",
    "PermissionDeniedError",
    "ProtocolError",
    "RateLimitedError",
    "TcordError",
]
