"""Authentication state mirrored from the engine's ``AuthSnapshot``."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .domain import User


class AuthKind(StrEnum):
    LOGGED_OUT = "logged_out"
    RESTORING = "restoring"
    WAITING_FOR_CREDENTIALS = "waiting_for_credentials"
    WAITING_FOR_MFA = "waiting_for_mfa"
    WAITING_FOR_QR = "waiting_for_qr"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


class MfaMethod(StrEnum):
    TOTP = "totp"
    SMS = "sms"
    BACKUP = "backup"


class AuthFailure(StrEnum):
    INVALID_CREDENTIALS = "invalid_credentials"
    INVALID_CODE = "invalid_code"
    CAPTCHA_REQUIRED = "captcha_required"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    QR_CANCELLED = "qr_cancelled"
    QR_EXPIRED = "qr_expired"
    PHONE_VERIFICATION_REQUIRED = "phone_verification_required"
    ACCOUNT_DISABLED = "account_disabled"
    WEBAUTHN_UNSUPPORTED = "webauthn_unsupported"
    SESSION_EXPIRED = "session_expired"
    CREDENTIAL_STORE = "credential_store"


@dataclass(frozen=True, slots=True)
class QrUser:
    id: str
    discriminator: str
    avatar: str | None
    username: str


@dataclass(frozen=True, slots=True)
class AuthState:
    version: int
    kind: AuthKind
    mfa_methods: tuple[MfaMethod, ...] = ()
    qr_url: str | None = None
    qr_expires_at: datetime | None = None
    qr_user: QrUser | None = None
    user: User | None = None
    failure: AuthFailure | None = None
    retry_after: float | None = None
    session_persisted: bool = False
