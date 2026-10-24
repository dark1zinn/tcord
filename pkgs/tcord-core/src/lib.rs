mod remote_auth;

use std::sync::{Arc, Mutex, mpsc};
use std::thread;

use keyring::Entry;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use reqwest::{Client, StatusCode};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::runtime::Runtime;
use tokio_util::sync::CancellationToken;

const API: &str = "https://discord.com/api/v9";
const SERVICE: &str = "tcord";
const ACCOUNT: &str = "default";

#[derive(Clone)]
pub(crate) enum AuthStatus {
    LoggedOut,
    WaitingForCredentials,
    WaitingForQr { payload: String, expires_at: String },
    WaitingForMfa { kind: String },
    Authenticated { user_id: String, username: String },
    Failed { message: String },
}

pub(crate) struct SessionData {
    status: AuthStatus,
    token: Option<String>,
    mfa_ticket: Option<String>,
    session_will_not_be_saved: bool,
}

#[pyclass(frozen)]
struct AuthState {
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    qr_payload: Option<String>,
    #[pyo3(get)]
    expires_at: Option<String>,
    #[pyo3(get)]
    mfa_kind: Option<String>,
    #[pyo3(get)]
    user_id: Option<String>,
    #[pyo3(get)]
    username: Option<String>,
    #[pyo3(get)]
    message: Option<String>,
    #[pyo3(get)]
    session_will_not_be_saved: bool,
}

impl AuthState {
    fn from(data: &SessionData) -> Self {
        let (kind, qr_payload, expires_at, mfa_kind, user_id, username, message) =
            match &data.status {
                AuthStatus::LoggedOut => ("logged_out", None, None, None, None, None, None),
                AuthStatus::WaitingForCredentials => (
                    "waiting_for_credentials",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                AuthStatus::WaitingForQr {
                    payload,
                    expires_at,
                } => (
                    "waiting_for_qr",
                    Some(payload.clone()),
                    Some(expires_at.clone()),
                    None,
                    None,
                    None,
                    None,
                ),
                AuthStatus::WaitingForMfa { kind } => (
                    "waiting_for_mfa",
                    None,
                    None,
                    Some(kind.clone()),
                    None,
                    None,
                    None,
                ),
                AuthStatus::Authenticated { user_id, username } => (
                    "authenticated",
                    None,
                    None,
                    None,
                    Some(user_id.clone()),
                    Some(username.clone()),
                    None,
                ),
                AuthStatus::Failed { message } => (
                    "failed",
                    None,
                    None,
                    None,
                    None,
                    None,
                    Some(message.clone()),
                ),
            };
        Self {
            kind: kind.into(),
            qr_payload,
            expires_at,
            mfa_kind,
            user_id,
            username,
            message,
            session_will_not_be_saved: data.session_will_not_be_saved,
        }
    }
}
#[pymethods]
impl AuthState {
    fn __repr__(&self) -> String {
        format!("AuthState(kind={:?})", self.kind)
    }
}

#[derive(Deserialize)]
struct CurrentUser {
    id: String,
    username: String,
}
enum Command {
    Password(String, String, CancellationToken),
    Mfa(String, CancellationToken),
    Resume(CancellationToken),
    Logout,
    Qr(CancellationToken),
}

#[pyclass]
struct Session {
    sender: mpsc::Sender<Command>,
    data: Arc<Mutex<SessionData>>,
    active: Arc<Mutex<Option<CancellationToken>>>,
}

#[pymethods]
impl Session {
    #[new]
    #[pyo3(signature = (api_base=None))]
    fn new(api_base: Option<String>) -> PyResult<Self> {
        let data = Arc::new(Mutex::new(SessionData {
            status: AuthStatus::LoggedOut,
            token: None,
            mfa_ticket: None,
            session_will_not_be_saved: false,
        }));
        let (sender, receiver) = mpsc::channel();
        let worker_data = Arc::clone(&data);
        let base = api_base
            .unwrap_or_else(|| API.into())
            .trim_end_matches('/')
            .to_owned();
        thread::Builder::new()
            .name("tcord-discord-worker".into())
            .spawn(move || Worker::new(worker_data, base).run(receiver))
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self {
            sender,
            data,
            active: Arc::new(Mutex::new(None)),
        })
    }

    fn begin_credentials(&self) -> AuthState {
        set_status(&self.data, AuthStatus::WaitingForCredentials);
        self.auth_state()
    }
    fn login_with_password(&self, email: String, password: String) -> PyResult<AuthState> {
        if email.is_empty() || password.is_empty() {
            return Err(PyValueError::new_err("email and password are required"));
        }
        set_status(&self.data, AuthStatus::WaitingForCredentials);
        self.send_with_token(|token| Command::Password(email, password, token))
    }
    fn submit_mfa(&self, code: String) -> PyResult<AuthState> {
        if code.is_empty() {
            return Err(PyValueError::new_err("authentication code is required"));
        }
        self.send_with_token(|token| Command::Mfa(code, token))
    }
    fn begin_qr_login(&self) -> PyResult<AuthState> {
        set_status(
            &self.data,
            AuthStatus::WaitingForQr {
                payload: "connecting".into(),
                expires_at: "pending".into(),
            },
        );
        self.send_with_token(Command::Qr)
    }
    fn resume_stored_session(&self) -> PyResult<AuthState> {
        self.send_with_token(Command::Resume)
    }
    fn logout(&self) -> PyResult<AuthState> {
        if let Some(token) = self
            .active
            .lock()
            .expect("active token lock poisoned")
            .take()
        {
            token.cancel();
        }
        set_status(&self.data, AuthStatus::LoggedOut);
        self.sender
            .send(Command::Logout)
            .map_err(|_| PyValueError::new_err("Discord worker stopped"))?;
        Ok(self.auth_state())
    }
    fn auth_state(&self) -> AuthState {
        AuthState::from(&self.data.lock().expect("session lock poisoned"))
    }
}

impl Session {
    fn send_with_token(
        &self,
        make_command: impl FnOnce(CancellationToken) -> Command,
    ) -> PyResult<AuthState> {
        if let Some(token) = self
            .active
            .lock()
            .expect("active token lock poisoned")
            .take()
        {
            token.cancel();
        }
        let token = CancellationToken::new();
        self.sender
            .send(make_command(token.clone()))
            .map_err(|_| PyValueError::new_err("Discord worker stopped"))?;
        *self.active.lock().expect("active token lock poisoned") = Some(token);
        Ok(self.auth_state())
    }
}

struct Worker {
    runtime: Runtime,
    http: Client,
    base: String,
    data: Arc<Mutex<SessionData>>,
}
impl Worker {
    fn new(data: Arc<Mutex<SessionData>>, base: String) -> Self {
        Self {
            runtime: Runtime::new().expect("Tokio runtime"),
            http: Client::builder()
                .timeout(std::time::Duration::from_secs(15))
                .user_agent("Tcord/0.1")
                .build()
                .expect("HTTP client"),
            base,
            data,
        }
    }
    fn run(self, receiver: mpsc::Receiver<Command>) {
        while let Ok(command) = receiver.recv() {
            match command {
                Command::Password(email, password, token) => {
                    self.runtime.block_on(self.login(email, password, token))
                }
                Command::Mfa(code, token) => self.runtime.block_on(self.mfa(code, token)),
                Command::Resume(token) => self.runtime.block_on(self.resume(token)),
                Command::Qr(token) => self.runtime.block_on(self.qr(token)),
                Command::Logout => {
                    let _ = delete_token();
                }
            }
        }
    }
    async fn login(&self, email: String, password: String, token: CancellationToken) {
        let response = tokio::select! { _ = token.cancelled() => return, response = self.http.post(format!("{}/auth/login", self.base)).json(&json!({"login":email,"password":password})).send() => response };
        self.handle(response, token).await;
    }
    async fn mfa(&self, code: String, token: CancellationToken) {
        let ticket = self
            .data
            .lock()
            .expect("session lock poisoned")
            .mfa_ticket
            .clone();
        let Some(ticket) = ticket else {
            return;
        };
        let response = tokio::select! { _ = token.cancelled() => return, response = self.http.post(format!("{}/auth/mfa/totp", self.base)).json(&json!({"ticket":ticket,"code":code,"login_source":Value::Null})).send() => response };
        self.handle(response, token).await;
    }
    async fn resume(&self, token: CancellationToken) {
        if token.is_cancelled() {
            return;
        }
        match load_token() {
            Ok(Some(value)) => self.accept(value, token).await,
            Ok(None) | Err(()) => set_status(&self.data, AuthStatus::WaitingForCredentials),
        }
    }
    async fn qr(&self, token: CancellationToken) {
        let result = tokio::select! { _ = token.cancelled() => return, result = remote_auth::authenticate(Arc::clone(&self.data)) => result };
        if let Ok(value) = result {
            self.accept(value, token).await;
        }
    }
    async fn handle(
        &self,
        response: Result<reqwest::Response, reqwest::Error>,
        token: CancellationToken,
    ) {
        if token.is_cancelled() {
            return;
        }
        let Ok(response) = response else {
            set_status(
                &self.data,
                AuthStatus::Failed {
                    message: "Unable to reach Discord".into(),
                },
            );
            return;
        };
        let status = response.status();
        let body = tokio::select! { _ = token.cancelled() => return, body = response.json::<Value>() => body.unwrap_or(Value::Null) };
        if token.is_cancelled() {
            return;
        }
        if body.get("captcha_key").is_some() || body.get("captcha_sitekey").is_some() {
            set_status(
                &self.data,
                AuthStatus::Failed {
                    message: "Discord requires CAPTCHA completion".into(),
                },
            );
        } else if status == StatusCode::TOO_MANY_REQUESTS {
            set_status(
                &self.data,
                AuthStatus::Failed {
                    message: "Discord rate limited this login attempt".into(),
                },
            );
        } else if !status.is_success() {
            set_status(
                &self.data,
                AuthStatus::Failed {
                    message: "Discord rejected the request".into(),
                },
            );
        } else if let Some(ticket) = body.get("ticket").and_then(Value::as_str) {
            let mut data = self.data.lock().expect("session lock poisoned");
            data.mfa_ticket = Some(ticket.into());
            data.status = AuthStatus::WaitingForMfa {
                kind: "totp".into(),
            };
        } else if let Some(value) = body.get("token").and_then(Value::as_str) {
            self.accept(value.into(), token).await;
        }
    }
    async fn accept(&self, value: String, token: CancellationToken) {
        if token.is_cancelled() {
            return;
        }
        let response = tokio::select! { _ = token.cancelled() => return, response = self.http.get(format!("{}/users/@me", self.base)).header("Authorization", &value).send() => response };
        let user = match response {
            Ok(response) => match response.error_for_status() {
                Ok(response) => {
                    tokio::select! { _ = token.cancelled() => return, user = response.json::<CurrentUser>() => user.ok() }
                }
                Err(_) => None,
            },
            Err(_) => None,
        };
        if token.is_cancelled() {
            return;
        }
        let Some(user) = user else {
            set_status(&self.data, AuthStatus::WaitingForCredentials);
            return;
        };
        let memory_only = persist_token(&value).is_err();
        if token.is_cancelled() {
            return;
        }
        let mut data = self.data.lock().expect("session lock poisoned");
        data.token = Some(value);
        data.mfa_ticket = None;
        data.session_will_not_be_saved = memory_only;
        data.status = AuthStatus::Authenticated {
            user_id: user.id,
            username: user.username,
        };
    }
}

pub(crate) fn set_status(data: &Arc<Mutex<SessionData>>, status: AuthStatus) {
    data.lock().expect("session lock poisoned").status = status;
}
fn entry() -> Result<Entry, ()> {
    Entry::new(SERVICE, ACCOUNT).map_err(|_| ())
}
fn persist_token(token: &str) -> Result<(), ()> {
    entry()?.set_password(token).map_err(|_| ())
}
fn load_token() -> Result<Option<String>, ()> {
    match entry()?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err(()),
    }
}
fn delete_token() -> Result<(), ()> {
    match entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err(()),
    }
}

#[pymodule]
fn tcord_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Session>()?;
    module.add_class::<AuthState>()?;
    Ok(())
}
