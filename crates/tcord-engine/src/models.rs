use serde::Deserialize;

/// Authentication state machine of a [`crate::Session`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AuthStatus {
    LoggedOut,
    WaitingForCredentials,
    WaitingForQr { payload: String, expires_at: String },
    WaitingForMfa { kind: String },
    Authenticated { user_id: String, username: String },
    Failed { message: String },
}

/// Flattened, presentation-safe view of the authentication state.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthView {
    pub kind: &'static str,
    pub qr_payload: Option<String>,
    pub expires_at: Option<String>,
    pub mfa_kind: Option<String>,
    pub user_id: Option<String>,
    pub username: Option<String>,
    pub message: Option<String>,
    pub session_will_not_be_saved: bool,
}

impl AuthView {
    pub(crate) fn new(status: &AuthStatus, session_will_not_be_saved: bool) -> Self {
        let mut view = Self {
            kind: "logged_out",
            qr_payload: None,
            expires_at: None,
            mfa_kind: None,
            user_id: None,
            username: None,
            message: None,
            session_will_not_be_saved,
        };
        match status {
            AuthStatus::LoggedOut => {}
            AuthStatus::WaitingForCredentials => view.kind = "waiting_for_credentials",
            AuthStatus::WaitingForQr {
                payload,
                expires_at,
            } => {
                view.kind = "waiting_for_qr";
                view.qr_payload = Some(payload.clone());
                view.expires_at = Some(expires_at.clone());
            }
            AuthStatus::WaitingForMfa { kind } => {
                view.kind = "waiting_for_mfa";
                view.mfa_kind = Some(kind.clone());
            }
            AuthStatus::Authenticated { user_id, username } => {
                view.kind = "authenticated";
                view.user_id = Some(user_id.clone());
                view.username = Some(username.clone());
            }
            AuthStatus::Failed { message } => {
                view.kind = "failed";
                view.message = Some(message.clone());
            }
        }
        view
    }
}

#[derive(Deserialize)]
pub(crate) struct CurrentUser {
    pub id: String,
    pub username: String,
}
