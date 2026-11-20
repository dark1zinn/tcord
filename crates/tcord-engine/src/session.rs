use std::sync::Arc;

use reqwest::Method;
use serde_json::{Value, json};
use tokio::runtime::Runtime;

use crate::auth::AuthController;
use crate::credentials::{CredentialStore, KeyringStore, MemoryStore};
use crate::error::{EngineError, Result};
use crate::http::DiscordHttp;
use crate::models::{AuthSnapshot, MfaMethod};

#[derive(Clone, Debug, Default)]
pub struct SessionConfig {
    pub api_base: Option<String>,
    pub remote_auth_url: Option<String>,
    pub memory_store: bool,
}

/// A Discord account session. Auth operations run in the background; REST calls block.
pub struct Session {
    runtime: Runtime,
    auth: Arc<AuthController>,
    http: Arc<DiscordHttp>,
}

impl Session {
    pub fn new(config: SessionConfig) -> Result<Self> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("tcord-engine")
            .enable_all()
            .build()
            .map_err(|error| EngineError::Network(error.to_string()))?;
        let http = Arc::new(DiscordHttp::new(config.api_base)?);
        let store: Arc<dyn CredentialStore> = if config.memory_store {
            Arc::new(MemoryStore::default())
        } else {
            Arc::new(KeyringStore::new())
        };
        let auth = Arc::new(AuthController::new(
            Arc::clone(&http),
            store,
            config.remote_auth_url,
        ));
        Ok(Self {
            runtime,
            auth,
            http,
        })
    }

    pub fn auth(&self) -> &Arc<AuthController> {
        &self.auth
    }

    pub fn auth_state(&self) -> AuthSnapshot {
        self.auth.snapshot()
    }

    pub fn login_with_password(&self, login: String, password: String) -> Result<AuthSnapshot> {
        if login.trim().is_empty() || password.is_empty() {
            return Err(EngineError::InvalidInput(
                "login and password are required".into(),
            ));
        }
        let auth = Arc::clone(&self.auth);
        self.runtime
            .spawn(async move { auth.login_password(&login, &password).await });
        Ok(self.auth.snapshot())
    }

    pub fn submit_mfa(&self, method: MfaMethod, code: String) -> Result<AuthSnapshot> {
        if code.trim().is_empty() {
            return Err(EngineError::InvalidInput(
                "authentication code is required".into(),
            ));
        }
        let auth = Arc::clone(&self.auth);
        self.runtime
            .spawn(async move { auth.submit_mfa(method, &code).await });
        Ok(self.auth.snapshot())
    }

    pub fn begin_qr_login(&self) -> AuthSnapshot {
        let auth = Arc::clone(&self.auth);
        self.runtime.spawn(async move { auth.start_qr().await });
        self.auth.snapshot()
    }

    pub fn resume_stored_session(&self) -> AuthSnapshot {
        let auth = Arc::clone(&self.auth);
        self.runtime.spawn(async move { auth.resume().await });
        self.auth.snapshot()
    }

    pub fn logout(&self) -> AuthSnapshot {
        self.runtime.block_on(self.auth.logout())
    }

    fn get(&self, path: &str, query: &[(&str, String)]) -> Result<Value> {
        let token = self.auth.token().ok_or(EngineError::Unauthorized)?;
        self.runtime
            .block_on(self.http.send(Method::GET, path, Some(&token), query, None))
    }

    pub fn get_guilds(&self) -> Result<String> {
        self.get("/users/@me/guilds", &[])
            .map(|value| value.to_string())
    }

    pub fn get_channels(&self, guild_id: &str) -> Result<String> {
        self.get(&format!("/guilds/{guild_id}/channels"), &[])
            .map(|value| value.to_string())
    }

    pub fn get_dms(&self) -> Result<String> {
        self.get("/users/@me/channels", &[])
            .map(|value| value.to_string())
    }

    pub fn load_message_history(
        &self,
        channel_id: &str,
        before: Option<String>,
        limit: u16,
    ) -> Result<String> {
        let mut query = vec![("limit", limit.to_string())];
        if let Some(before) = before {
            query.push(("before", before));
        }
        self.get(&format!("/channels/{channel_id}/messages"), &query)
            .map(|value| value.to_string())
    }

    pub fn send_message(&self, channel_id: &str, content: &str) -> Result<String> {
        if content.trim().is_empty() {
            return Err(EngineError::InvalidInput(
                "message content must not be empty".into(),
            ));
        }
        let token = self.auth.token().ok_or(EngineError::Unauthorized)?;
        let body = json!({"content": content});
        self.runtime
            .block_on(self.http.send(
                Method::POST,
                &format!("/channels/{channel_id}/messages"),
                Some(&token),
                &[],
                Some(&body),
            ))
            .map(|value| value.to_string())
    }
}
