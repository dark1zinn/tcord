"""User-facing messages for client errors."""

from __future__ import annotations

from client.errors import (
    AuthenticationError,
    CancelledError,
    CaptchaRequiredError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    TcordError,
)

from .i18n import _


def captcha_text() -> str:
    return _(
        "Discord asked for a CAPTCHA. CAPTCHA solving isn't supported yet — "
        "try QR login or retry later."
    )


def describe(error: BaseException) -> str | None:
    """Localized notification text, or ``None`` when the error needs no notification."""
    match error:
        case RateLimitedError():
            return _("Slow down — try again in {seconds}s").format(
                seconds=max(1, round(error.retry_after))
            )
        case CaptchaRequiredError():
            return captcha_text()
        case AuthenticationError() | CancelledError():
            return None  # surfaced through the auth state
        case NetworkError():
            return _("Can't reach Discord")
        case PermissionDeniedError():
            return _("You don't have permission to do that.")
        case NotFoundError():
            return _("That no longer exists.")
        case InvalidInputError():
            return str(error)
        case TcordError():
            return _("Something went wrong: {message}").format(message=error)
    return _("Something went wrong: {message}").format(message=error)
