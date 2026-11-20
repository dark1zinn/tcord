use std::sync::{Arc, Mutex};

use reqwest::Method;
use secrecy::SecretString;
use serde_json::{Value, json};
use tokio::sync::watch;
use tokio_util::sync::CancellationToken;

use crate::credentials::CredentialStore;
use crate::error::{EngineError, Result};
use crate::http::DiscordHttp;
use crate::models::{AuthFailure, AuthKind, AuthSnapshot, MfaMethod, User};
use crate::remote_auth::{self, QrEvent};

pub const DEFAULT_REMOTE_AUTH_URL: &str = "wss://remote-auth-gateway.discord.gg/?v=2";

struct AuthInner {
    generation: u64,
    op_cancel: CancellationToken,
    token: Option<SecretString>,
    mfa_ticket: Option<String>,
    login_instance_id: Option<String>,
    remember: bool,
}

/// Owns the token and the authentication state machine.
///
/// Every operation starts a new *generation* that cancels the previous one; results of a stale
/// generation are discarded, so a late login response can never override a logout.
pub struct AuthController {
    http: Arc<DiscordHttp>,
    store: Arc<dyn CredentialStore>,
    remote_auth_url: String,
    state: watch::Sender<AuthSnapshot>,
    inner: Mutex<AuthInner>,
}

/// Identifies the operation a result belongs to.
#[derive(Clone)]
pub struct Op {
    generation: u64,
    cancel: CancellationToken,
}

impl AuthController {
    pub fn new(
        http: Arc<DiscordHttp>,
        store: Arc<dyn CredentialStore>,
        remote_auth_url: Option<String>,
    ) -> Self {
        let (state, _) = watch::channel(AuthSnapshot::default());
        Self {
            http,
            store,
            remote_auth_url: remote_auth_url.unwrap_or_else(|| DEFAULT_REMOTE_AUTH_URL.into()),
            state,
            inner: Mutex::new(AuthInner {
                generation: 0,
                op_cancel: CancellationToken::new(),
                token: None,
                mfa_ticket: None,
                login_instance_id: None,
                remember: true,
            }),
        }
    }

    pub fn snapshot(&self) -> AuthSnapshot {
        self.state.borrow().clone()
    }

    pub fn subscribe(&self) -> watch::Receiver<AuthSnapshot> {
        self.state.subscribe()
    }

    /// Wait until the snapshot version exceeds `after_version`, then return it.
    pub async fn wait_change(&self, after_version: u64) -> AuthSnapshot {
        let mut receiver = self.subscribe();
        let result = receiver
            .wait_for(|snapshot| snapshot.version > after_version)
            .await
            .map(|snapshot| snapshot.clone());
        result.unwrap_or_else(|_| self.snapshot())
    }

    pub fn token(&self) -> Option<SecretString> {
        self.lock().token.clone()
    }

    pub fn remember_session(&self) -> bool {
        self.lock().remember
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, AuthInner> {
        self.inner.lock().expect("auth state poisoned")
    }

    /// Cancel the running operation and start a new generation.
    pub fn begin_op(&self) -> Op {
        let mut inner = self.lock();
        inner.op_cancel.cancel();
        inner.generation += 1;
        inner.op_cancel = CancellationToken::new();
        Op {
            generation: inner.generation,
            cancel: inner.op_cancel.clone(),
        }
    }

    /// Apply `update` to the auth inner state and published snapshot if `op` is still current.
    fn commit(&self, op: &Op, update: impl FnOnce(&mut AuthInner, &mut AuthSnapshot)) -> bool {
        let mut inner = self.lock();
        if inner.generation != op.generation {
            return false;
        }
        self.state.send_modify(|snapshot| {
            update(&mut inner, snapshot);
            snapshot.version += 1;
        });
        true
    }

    fn publish(&self, op: &Op, kind: AuthKind, failure: Option<AuthFailure>) -> bool {
        self.commit(op, |_, snapshot| set_kind(snapshot, kind, failure))
    }

    /// Restore a persisted session, if any.
    pub async fn resume(&self) -> AuthSnapshot {
        let op = self.begin_op();
        self.publish(&op, AuthKind::Restoring, None);
        let store = Arc::clone(&self.store);
        let loaded = tokio::task::spawn_blocking(move || store.load())
            .await
            .unwrap_or_else(|error| Err(EngineError::CredentialStore(error.to_string())));
        match loaded {
            Ok(Some(token)) => self.accept(&op, token, false).await,
            Ok(None) => {
                self.publish(&op, AuthKind::WaitingForCredentials, None);
            }
            Err(_) => {
                self.publish(
                    &op,
                    AuthKind::WaitingForCredentials,
                    Some(AuthFailure::CredentialStore),
                );
            }
        }
        self.snapshot()
    }

    /// Log in with email (or phone) and password.
    pub async fn login_password(&self, login: &str, password: &str) -> Result<AuthSnapshot> {
        if login.trim().is_empty() || password.is_empty() {
            return Err(EngineError::InvalidInput(
                "login and password are required".into(),
            ));
        }
        let op = self.begin_op();
        self.publish(&op, AuthKind::WaitingForCredentials, None);
        let body = json!({"login": login, "password": password, "undelete": false});
        let response = tokio::select! {
            _ = op.cancel.cancelled() => return Ok(self.snapshot()),
            response = self.http.send_raw(Method::POST, "/auth/login", None, &[], Some(&body)) => response,
        };
        self.handle_login_response(&op, response, AuthKind::WaitingForCredentials)
            .await;
        Ok(self.snapshot())
    }

    /// Complete a pending MFA challenge.
    pub async fn submit_mfa(&self, method: MfaMethod, code: &str) -> Result<AuthSnapshot> {
        if code.trim().is_empty() {
            return Err(EngineError::InvalidInput(
                "authentication code is required".into(),
            ));
        }
        let (ticket, instance) = {
            let inner = self.lock();
            (inner.mfa_ticket.clone(), inner.login_instance_id.clone())
        };
        let Some(ticket) = ticket else {
            return Err(EngineError::InvalidInput(
                "no MFA challenge is pending".into(),
            ));
        };
        let snapshot = self.snapshot();
        if snapshot.kind != AuthKind::WaitingForMfa {
            return Err(EngineError::InvalidInput(
                "no MFA challenge is pending".into(),
            ));
        }
        if !snapshot.mfa_methods.contains(&method) {
            return Err(EngineError::InvalidInput(format!(
                "MFA method {} is not available for this account",
                method.as_str()
            )));
        }
        let op = self.begin_op();
        let body = json!({"ticket": ticket, "code": code.trim(), "login_instance_id": instance});
        let path = format!("/auth/mfa/{}", method.as_str());
        let response = tokio::select! {
            _ = op.cancel.cancelled() => return Ok(self.snapshot()),
            response = self.http.send_raw(Method::POST, &path, None, &[], Some(&body)) => response,
        };
        self.handle_login_response(&op, response, AuthKind::WaitingForMfa)
            .await;
        Ok(self.snapshot())
    }

    /// Ask Discord to text an MFA code. Returns the redacted phone number it was sent to.
    pub async fn send_mfa_sms(&self) -> Result<Option<String>> {
        let snapshot = self.snapshot();
        if snapshot.kind != AuthKind::WaitingForMfa
            || !snapshot.mfa_methods.contains(&MfaMethod::Sms)
        {
            return Err(EngineError::InvalidInput(
                "SMS verification is not available".into(),
            ));
        }
        let ticket = self
            .lock()
            .mfa_ticket
            .clone()
            .ok_or_else(|| EngineError::InvalidInput("no MFA challenge is pending".into()))?;
        let body = json!({"ticket": ticket});
        let response = self
            .http
            .send(Method::POST, "/auth/mfa/sms/send", None, &[], Some(&body))
            .await?;
        Ok(response
            .get("phone")
            .and_then(Value::as_str)
            .map(str::to_owned))
    }

    async fn handle_login_response(
        &self,
        op: &Op,
        response: Result<crate::http::RawResponse>,
        retry_kind: AuthKind,
    ) {
        let response = match response {
            Ok(response) => response,
            Err(_) => {
                self.publish(op, retry_kind, Some(AuthFailure::Network));
                return;
            }
        };
        let body = &response.body;
        if (200..300).contains(&response.status) {
            if let Some(token) = body.get("token").and_then(Value::as_str) {
                self.accept(op, SecretString::from(token.to_owned()), true)
                    .await;
                return;
            }
            if let Some(ticket) = body.get("ticket").and_then(Value::as_str) {
                let methods = mfa_methods(body);
                let instance = body
                    .get("login_instance_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let ticket = ticket.to_owned();
                if methods.is_empty() {
                    self.publish(op, AuthKind::Failed, Some(AuthFailure::WebauthnUnsupported));
                    return;
                }
                self.commit(op, |inner, snapshot| {
                    inner.mfa_ticket = Some(ticket);
                    inner.login_instance_id = instance;
                    set_kind(snapshot, AuthKind::WaitingForMfa, None);
                    snapshot.mfa_methods = methods;
                });
                return;
            }
            self.publish(op, retry_kind, Some(AuthFailure::InvalidCredentials));
            return;
        }
        let failure = login_failure(response.status, body, retry_kind);
        let retry_after = body.get("retry_after").and_then(Value::as_f64);
        let methods = self.snapshot().mfa_methods;
        let kind = if failure == AuthFailure::AccountDisabled
            || failure == AuthFailure::PhoneVerificationRequired
        {
            AuthKind::Failed
        } else {
            retry_kind
        };
        self.commit(op, |_, snapshot| {
            set_kind(snapshot, kind, Some(failure));
            snapshot.retry_after = retry_after;
            if kind == AuthKind::WaitingForMfa {
                snapshot.mfa_methods = methods;
            }
        });
    }

    /// Start QR (remote-auth) login. Progress is published through snapshots.
    pub async fn start_qr(&self) -> AuthSnapshot {
        let op = self.begin_op();
        self.publish(&op, AuthKind::WaitingForQr, None);
        let on_event = |event: QrEvent| {
            self.commit(&op, |_, snapshot| match event {
                QrEvent::Ready { url, expires_at } => {
                    snapshot.qr_url = Some(url);
                    snapshot.qr_expires_at = expires_at;
                    snapshot.qr_user = None;
                }
                QrEvent::Scanned(user) => snapshot.qr_user = Some(user),
            });
        };
        let result = tokio::select! {
            _ = op.cancel.cancelled() => return self.snapshot(),
            result = remote_auth::authenticate(&self.remote_auth_url, &self.http, &on_event) => result,
        };
        match result {
            Ok(token) => self.accept(&op, token, true).await,
            Err(EngineError::Cancelled) => {
                self.publish(&op, AuthKind::Failed, Some(AuthFailure::QrCancelled));
            }
            Err(EngineError::Protocol(reason)) if reason == "expired" => {
                self.publish(&op, AuthKind::Failed, Some(AuthFailure::QrExpired));
            }
            Err(_) => {
                self.publish(&op, AuthKind::Failed, Some(AuthFailure::Network));
            }
        }
        self.snapshot()
    }

    /// Abort a running QR login and go back to the credential form.
    pub fn cancel_qr(&self) {
        if self.snapshot().kind == AuthKind::WaitingForQr {
            let op = self.begin_op();
            self.publish(&op, AuthKind::WaitingForCredentials, None);
        }
    }

    /// Forget the session: cancels running work, clears the token, deletes the stored copy.
    pub async fn logout(&self) -> AuthSnapshot {
        let op = self.begin_op();
        self.commit(&op, |inner, snapshot| {
            inner.token = None;
            inner.mfa_ticket = None;
            inner.login_instance_id = None;
            set_kind(snapshot, AuthKind::LoggedOut, None);
            snapshot.user = None;
            snapshot.session_persisted = false;
        });
        if let Err(_error) = self.delete_stored().await {
            self.publish(&op, AuthKind::LoggedOut, Some(AuthFailure::CredentialStore));
        }
        self.snapshot()
    }

    /// Drop a session Discord rejected (HTTP 401 / gateway 4004).
    pub fn invalidate(&self) {
        let op = self.begin_op();
        self.commit(&op, |inner, snapshot| {
            inner.token = None;
            inner.mfa_ticket = None;
            set_kind(
                snapshot,
                AuthKind::LoggedOut,
                Some(AuthFailure::SessionExpired),
            );
            snapshot.user = None;
            snapshot.session_persisted = false;
        });
        let store = Arc::clone(&self.store);
        tokio::task::spawn_blocking(move || store.delete());
    }

    /// Choose whether future (and the current) sessions are kept in the credential store.
    pub async fn set_remember_session(&self, remember: bool) -> AuthSnapshot {
        let token = {
            let mut inner = self.lock();
            inner.remember = remember;
            inner.token.clone()
        };
        let persisted = match (remember, token) {
            (true, Some(token)) => self.save_stored(token).await.is_ok(),
            (false, _) => {
                let _ = self.delete_stored().await;
                false
            }
            (true, None) => false,
        };
        self.state.send_modify(|snapshot| {
            snapshot.session_persisted = persisted && snapshot.kind == AuthKind::Authenticated;
            snapshot.version += 1;
        });
        self.snapshot()
    }

    async fn accept(&self, op: &Op, token: SecretString, save: bool) {
        let response = tokio::select! {
            _ = op.cancel.cancelled() => return,
            response = self.http.send(Method::GET, "/users/@me", Some(&token), &[], None) => response,
        };
        let user: User = match response.and_then(|body| {
            serde_json::from_value(body).map_err(|error| EngineError::Protocol(error.to_string()))
        }) {
            Ok(user) => user,
            Err(EngineError::Unauthorized) => {
                if !save {
                    let _ = self.delete_stored().await;
                }
                self.publish(
                    op,
                    AuthKind::WaitingForCredentials,
                    Some(AuthFailure::SessionExpired),
                );
                return;
            }
            Err(_) => {
                self.publish(op, AuthKind::Failed, Some(AuthFailure::Network));
                return;
            }
        };
        let persisted = if self.remember_session() {
            if save {
                self.save_stored(token.clone()).await.is_ok()
            } else {
                true
            }
        } else {
            false
        };
        self.commit(op, |inner, snapshot| {
            inner.token = Some(token);
            inner.mfa_ticket = None;
            inner.login_instance_id = None;
            set_kind(snapshot, AuthKind::Authenticated, None);
            snapshot.user = Some(user);
            snapshot.session_persisted = persisted;
        });
    }

    async fn save_stored(&self, token: SecretString) -> Result<()> {
        let store = Arc::clone(&self.store);
        tokio::task::spawn_blocking(move || store.save(&token))
            .await
            .unwrap_or_else(|error| Err(EngineError::CredentialStore(error.to_string())))
    }

    async fn delete_stored(&self) -> Result<()> {
        let store = Arc::clone(&self.store);
        tokio::task::spawn_blocking(move || store.delete())
            .await
            .unwrap_or_else(|error| Err(EngineError::CredentialStore(error.to_string())))
    }
}

fn set_kind(snapshot: &mut AuthSnapshot, kind: AuthKind, failure: Option<AuthFailure>) {
    snapshot.kind = kind;
    snapshot.failure = failure;
    snapshot.retry_after = None;
    if kind != AuthKind::WaitingForMfa {
        snapshot.mfa_methods.clear();
    }
    if kind != AuthKind::WaitingForQr {
        snapshot.qr_url = None;
        snapshot.qr_expires_at = None;
        snapshot.qr_user = None;
    }
}

fn mfa_methods(body: &Value) -> Vec<MfaMethod> {
    [
        ("totp", MfaMethod::Totp),
        ("sms", MfaMethod::Sms),
        ("backup", MfaMethod::Backup),
    ]
    .into_iter()
    .filter(|(flag, _)| body.get(flag).and_then(Value::as_bool).unwrap_or(false))
    .map(|(_, method)| method)
    .collect()
}

fn login_failure(status: u16, body: &Value, retry_kind: AuthKind) -> AuthFailure {
    let code = body.get("code").and_then(Value::as_u64);
    if status == 400 && body.get("captcha_key").is_some() {
        return AuthFailure::CaptchaRequired;
    }
    match (status, code) {
        (429, _) => AuthFailure::RateLimited,
        (_, Some(70007)) => AuthFailure::PhoneVerificationRequired,
        (_, Some(20011 | 20013)) => AuthFailure::AccountDisabled,
        _ if retry_kind == AuthKind::WaitingForMfa => AuthFailure::InvalidCode,
        _ => AuthFailure::InvalidCredentials,
    }
}
