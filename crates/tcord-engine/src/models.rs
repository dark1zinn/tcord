use std::time::SystemTime;

use serde::Deserialize;

/// The signed-in Discord account (or another user referenced by the engine).
#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct User {
    pub id: String,
    pub username: String,
    #[serde(default)]
    pub global_name: Option<String>,
    /// Avatar hash (`a_` prefix when animated).
    #[serde(default)]
    pub avatar: Option<String>,
    #[serde(default)]
    pub bot: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AuthKind {
    LoggedOut,
    Restoring,
    WaitingForCredentials,
    WaitingForMfa,
    WaitingForQr,
    Authenticated,
    Failed,
}

impl AuthKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LoggedOut => "logged_out",
            Self::Restoring => "restoring",
            Self::WaitingForCredentials => "waiting_for_credentials",
            Self::WaitingForMfa => "waiting_for_mfa",
            Self::WaitingForQr => "waiting_for_qr",
            Self::Authenticated => "authenticated",
            Self::Failed => "failed",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MfaMethod {
    Totp,
    Sms,
    Backup,
}

impl MfaMethod {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Totp => "totp",
            Self::Sms => "sms",
            Self::Backup => "backup",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "totp" => Some(Self::Totp),
            "sms" => Some(Self::Sms),
            "backup" => Some(Self::Backup),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AuthFailure {
    InvalidCredentials,
    InvalidCode,
    CaptchaRequired,
    RateLimited,
    Network,
    QrCancelled,
    QrExpired,
    PhoneVerificationRequired,
    AccountDisabled,
    WebauthnUnsupported,
    SessionExpired,
    CredentialStore,
}

impl AuthFailure {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InvalidCredentials => "invalid_credentials",
            Self::InvalidCode => "invalid_code",
            Self::CaptchaRequired => "captcha_required",
            Self::RateLimited => "rate_limited",
            Self::Network => "network",
            Self::QrCancelled => "qr_cancelled",
            Self::QrExpired => "qr_expired",
            Self::PhoneVerificationRequired => "phone_verification_required",
            Self::AccountDisabled => "account_disabled",
            Self::WebauthnUnsupported => "webauthn_unsupported",
            Self::SessionExpired => "session_expired",
            Self::CredentialStore => "credential_store",
        }
    }
}

/// User shown while a scanned QR login awaits confirmation on the phone.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QrUser {
    pub id: String,
    pub discriminator: String,
    pub avatar: Option<String>,
    pub username: String,
}

/// Immutable snapshot of the authentication state machine; `version` increases on every change.
#[derive(Clone, Debug, PartialEq)]
pub struct AuthSnapshot {
    pub version: u64,
    pub kind: AuthKind,
    pub mfa_methods: Vec<MfaMethod>,
    pub qr_url: Option<String>,
    pub qr_expires_at: Option<SystemTime>,
    pub qr_user: Option<QrUser>,
    pub user: Option<User>,
    pub failure: Option<AuthFailure>,
    pub retry_after: Option<f64>,
    pub session_persisted: bool,
}

impl Default for AuthSnapshot {
    fn default() -> Self {
        Self {
            version: 0,
            kind: AuthKind::LoggedOut,
            mfa_methods: Vec::new(),
            qr_url: None,
            qr_expires_at: None,
            qr_user: None,
            user: None,
            failure: None,
            retry_after: None,
            session_persisted: false,
        }
    }
}
