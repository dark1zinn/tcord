use std::sync::{Arc, Mutex, mpsc};
use std::thread;

use reqwest::{Client, StatusCode};
use serde_json::{Value, json};
use tokio::runtime::Runtime;
use tokio_util::sync::CancellationToken;

use crate::credentials::{delete_token, load_token, persist_token};
use crate::models::{AuthStatus, AuthView, CurrentUser};
use crate::remote_auth;

const API: &str = "https://discord.com/api/v9";
const WORKER_STOPPED: &str = "Discord worker stopped";

pub(crate) struct SessionData {
    pub(crate) status: AuthStatus,
    token: Option<String>,
    mfa_ticket: Option<String>,
    session_will_not_be_saved: bool,
}

type Reply = mpsc::Sender<Result<String, String>>;

enum Command {
    Password(String, String, CancellationToken),
    Mfa(String, CancellationToken),
    Resume(CancellationToken),
    Logout,
    Qr(CancellationToken),
    Guilds(Reply),
    GuildChannels(String, Reply),
    DirectMessages(Reply),
    History(String, Option<String>, u16, Reply),
    SendMessage(String, String, Reply),
}

/// A Discord account session driven by a background worker thread.
pub struct Session {
    sender: mpsc::Sender<Command>,
    data: Arc<Mutex<SessionData>>,
    active: Arc<Mutex<Option<CancellationToken>>>,
}

impl Session {
    pub fn new(api_base: Option<String>) -> std::io::Result<Self> {
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
            .spawn(move || Worker::new(worker_data, base).run(receiver))?;
        Ok(Self {
            sender,
            data,
            active: Arc::new(Mutex::new(None)),
        })
    }

    pub fn begin_credentials(&self) -> AuthView {
        set_status(&self.data, AuthStatus::WaitingForCredentials);
        self.auth_state()
    }

    pub fn login_with_password(&self, email: String, password: String) -> Result<AuthView, String> {
        if email.is_empty() || password.is_empty() {
            return Err("email and password are required".into());
        }
        set_status(&self.data, AuthStatus::WaitingForCredentials);
        self.send_with_token(|token| Command::Password(email, password, token))
    }

    pub fn submit_mfa(&self, code: String) -> Result<AuthView, String> {
        if code.is_empty() {
            return Err("authentication code is required".into());
        }
        self.send_with_token(|token| Command::Mfa(code, token))
    }

    pub fn begin_qr_login(&self) -> Result<AuthView, String> {
        set_status(
            &self.data,
            AuthStatus::WaitingForQr {
                payload: "connecting".into(),
                expires_at: "pending".into(),
            },
        );
        self.send_with_token(Command::Qr)
    }

    pub fn resume_stored_session(&self) -> Result<AuthView, String> {
        self.send_with_token(Command::Resume)
    }

    pub fn logout(&self) -> Result<AuthView, String> {
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
            .map_err(|_| WORKER_STOPPED.to_string())?;
        Ok(self.auth_state())
    }

    pub fn auth_state(&self) -> AuthView {
        let data = self.data.lock().expect("session lock poisoned");
        AuthView::new(&data.status, data.session_will_not_be_saved)
    }

    pub fn get_guilds(&self) -> Result<String, String> {
        self.request(Command::Guilds)
    }

    pub fn get_channels(&self, guild_id: String) -> Result<String, String> {
        self.request(|reply| Command::GuildChannels(guild_id, reply))
    }

    pub fn get_dms(&self) -> Result<String, String> {
        self.request(Command::DirectMessages)
    }

    pub fn load_message_history(
        &self,
        channel_id: String,
        before: Option<String>,
        limit: u16,
    ) -> Result<String, String> {
        self.request(|reply| Command::History(channel_id, before, limit, reply))
    }

    pub fn send_message(&self, channel_id: String, content: String) -> Result<String, String> {
        if content.is_empty() {
            return Err("message content must not be empty".into());
        }
        self.request(|reply| Command::SendMessage(channel_id, content, reply))
    }

    fn send_with_token(
        &self,
        make_command: impl FnOnce(CancellationToken) -> Command,
    ) -> Result<AuthView, String> {
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
            .map_err(|_| WORKER_STOPPED.to_string())?;
        *self.active.lock().expect("active token lock poisoned") = Some(token);
        Ok(self.auth_state())
    }

    fn request(&self, make_command: impl FnOnce(Reply) -> Command) -> Result<String, String> {
        let (reply, receiver) = mpsc::channel();
        self.sender
            .send(make_command(reply))
            .map_err(|_| WORKER_STOPPED.to_string())?;
        receiver.recv().map_err(|_| WORKER_STOPPED.to_string())?
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
                Command::Guilds(reply) => self
                    .runtime
                    .block_on(self.get_json("/users/@me/guilds", reply)),
                Command::GuildChannels(guild_id, reply) => self
                    .runtime
                    .block_on(self.get_json(&format!("/guilds/{guild_id}/channels"), reply)),
                Command::DirectMessages(reply) => self
                    .runtime
                    .block_on(self.get_json("/users/@me/channels", reply)),
                Command::History(channel_id, before, limit, reply) => self
                    .runtime
                    .block_on(self.history(channel_id, before, limit, reply)),
                Command::SendMessage(channel_id, content, reply) => self
                    .runtime
                    .block_on(self.send_message(channel_id, content, reply)),
                Command::Logout => {
                    let _ = delete_token();
                }
            }
        }
    }

    fn session_token(&self) -> Result<String, String> {
        self.data
            .lock()
            .expect("session lock poisoned")
            .token
            .clone()
            .ok_or_else(|| "No authenticated Discord session".into())
    }

    async fn read_text(request: reqwest::RequestBuilder) -> Result<String, String> {
        request
            .send()
            .await
            .map_err(|_| "Unable to reach Discord".to_string())?
            .error_for_status()
            .map_err(|_| "Discord rejected the request".to_string())?
            .text()
            .await
            .map_err(|_| "Discord returned an invalid response".to_string())
    }

    async fn get_json(&self, path: &str, reply: Reply) {
        let result = match self.session_token() {
            Ok(token) => {
                Self::read_text(
                    self.http
                        .get(format!("{}{}", self.base, path))
                        .header("Authorization", token),
                )
                .await
            }
            Err(error) => Err(error),
        };
        let _ = reply.send(result);
    }

    async fn history(&self, channel_id: String, before: Option<String>, limit: u16, reply: Reply) {
        let result = match self.session_token() {
            Ok(token) => {
                let mut request = self
                    .http
                    .get(format!("{}/channels/{channel_id}/messages", self.base))
                    .header("Authorization", token)
                    .query(&[("limit", limit.to_string())]);
                if let Some(before) = before {
                    request = request.query(&[("before", before)]);
                }
                Self::read_text(request).await
            }
            Err(error) => Err(error),
        };
        let _ = reply.send(result);
    }

    async fn send_message(&self, channel_id: String, content: String, reply: Reply) {
        let result = match self.session_token() {
            Ok(token) => {
                Self::read_text(
                    self.http
                        .post(format!("{}/channels/{channel_id}/messages", self.base))
                        .header("Authorization", token)
                        .json(&json!({"content": content})),
                )
                .await
            }
            Err(error) => Err(error),
        };
        let _ = reply.send(result);
    }

    async fn login(&self, email: String, password: String, token: CancellationToken) {
        let request = self
            .http
            .post(format!("{}/auth/login", self.base))
            .json(&json!({"login": email, "password": password}))
            .send();
        let response = tokio::select! {
            _ = token.cancelled() => return,
            response = request => response,
        };
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
        let request = self
            .http
            .post(format!("{}/auth/mfa/totp", self.base))
            .json(&json!({"ticket": ticket, "code": code, "login_source": Value::Null}))
            .send();
        let response = tokio::select! {
            _ = token.cancelled() => return,
            response = request => response,
        };
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
        let result = tokio::select! {
            _ = token.cancelled() => return,
            result = remote_auth::authenticate(Arc::clone(&self.data)) => result,
        };
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
            fail(&self.data, "Unable to reach Discord");
            return;
        };
        let status = response.status();
        let body = tokio::select! {
            _ = token.cancelled() => return,
            body = response.json::<Value>() => body.unwrap_or(Value::Null),
        };
        if token.is_cancelled() {
            return;
        }
        if body.get("captcha_key").is_some() || body.get("captcha_sitekey").is_some() {
            fail(&self.data, "Discord requires CAPTCHA completion");
        } else if status == StatusCode::TOO_MANY_REQUESTS {
            fail(&self.data, "Discord rate limited this login attempt");
        } else if !status.is_success() {
            fail(&self.data, "Discord rejected the request");
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
        let request = self
            .http
            .get(format!("{}/users/@me", self.base))
            .header("Authorization", &value)
            .send();
        let response = tokio::select! {
            _ = token.cancelled() => return,
            response = request => response,
        };
        let user = match response.and_then(reqwest::Response::error_for_status) {
            Ok(response) => tokio::select! {
                _ = token.cancelled() => return,
                user = response.json::<CurrentUser>() => user.ok(),
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

fn fail(data: &Arc<Mutex<SessionData>>, message: &str) {
    set_status(
        data,
        AuthStatus::Failed {
            message: message.into(),
        },
    );
}
