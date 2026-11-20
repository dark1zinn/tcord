use std::sync::{Arc, Mutex};
use std::time::Duration;

use reqwest::Method;
use secrecy::SecretString;
use serde_json::{Value, json};
use tokio_util::sync::CancellationToken;

use crate::auth::AuthController;
use crate::credentials::{CredentialStore, KeyringStore, MemoryStore};
use crate::discord::{Channel, Guild, Message, MessageReference};
use crate::error::{EngineError, Result};
use crate::gateway::{GatewayConfig, GatewayEvent, GatewayHandle, Ready};
use crate::http::DiscordHttp;

#[derive(Clone, Debug, Default)]
pub struct SessionConfig {
    pub api_base: Option<String>,
    pub remote_auth_url: Option<String>,
    pub gateway_url: Option<String>,
    pub memory_store: bool,
    /// Discord locale such as `en-US`; defaults to `en-US`.
    pub locale: Option<String>,
    /// Base reconnect delay; defaults to one second (tests shrink it).
    pub reconnect_base: Option<Duration>,
}

/// A Discord account session: authentication, REST operations and the gateway.
pub struct Session {
    auth: Arc<AuthController>,
    http: Arc<DiscordHttp>,
    gateway_config: GatewayConfig,
    gateway: Mutex<Option<Arc<GatewayHandle>>>,
    closed: CancellationToken,
}

impl Session {
    pub fn new(config: SessionConfig) -> Result<Self> {
        let locale = config.locale.unwrap_or_else(|| "en-US".into());
        let http = Arc::new(DiscordHttp::with_locale(config.api_base, &locale)?);
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
        let defaults = GatewayConfig::default();
        let gateway_config = GatewayConfig {
            url: config.gateway_url.unwrap_or(defaults.url),
            locale,
            reconnect_base: config.reconnect_base.unwrap_or(defaults.reconnect_base),
        };
        Ok(Self {
            auth,
            http,
            gateway_config,
            gateway: Mutex::new(None),
            closed: CancellationToken::new(),
        })
    }

    pub fn auth(&self) -> &Arc<AuthController> {
        &self.auth
    }

    /// Cancel every running operation. The session can no longer authenticate afterwards.
    pub fn close(&self) {
        self.closed.cancel();
        self.disconnect_gateway();
        self.auth.begin_op();
    }

    fn current_gateway(&self) -> Option<Arc<GatewayHandle>> {
        self.gateway.lock().expect("gateway slot poisoned").clone()
    }

    /// Open the gateway (replacing any previous connection) and return the first READY.
    pub async fn connect_gateway(&self) -> Result<Ready> {
        if self.closed.is_cancelled() {
            return Err(EngineError::Cancelled);
        }
        self.disconnect_gateway();
        let (handle, ready) =
            GatewayHandle::connect(self.gateway_config.clone(), Arc::clone(&self.auth)).await?;
        *self.gateway.lock().expect("gateway slot poisoned") = Some(Arc::new(handle));
        Ok(ready)
    }

    /// Next gateway event; `None` when no gateway is running or it has stopped.
    pub async fn next_event(&self) -> Option<GatewayEvent> {
        let gateway = self.current_gateway()?;
        tokio::select! {
            _ = self.closed.cancelled() => None,
            event = gateway.next_event() => event,
        }
    }

    pub fn subscribe_guild(&self, guild_id: String) {
        if let Some(gateway) = self.current_gateway() {
            gateway.subscribe_guild(guild_id);
        }
    }

    pub fn disconnect_gateway(&self) {
        if let Some(gateway) = self.gateway.lock().expect("gateway slot poisoned").take() {
            gateway.disconnect();
        }
    }

    fn token(&self) -> Result<SecretString> {
        if self.closed.is_cancelled() {
            return Err(EngineError::Cancelled);
        }
        self.auth.token().ok_or(EngineError::Unauthorized)
    }

    async fn call(
        &self,
        method: Method,
        path: &str,
        query: &[(&str, String)],
        body: Option<&Value>,
    ) -> Result<Value> {
        let token = self.token()?;
        let result = tokio::select! {
            _ = self.closed.cancelled() => Err(EngineError::Cancelled),
            result = self.http.send(method, path, Some(&token), query, body) => result,
        };
        if matches!(result, Err(EngineError::Unauthorized)) {
            self.auth.invalidate_if_current(&token);
        }
        result
    }

    pub async fn fetch_guilds(&self) -> Result<Vec<Guild>> {
        let value = self
            .call(Method::GET, "/users/@me/guilds", &[], None)
            .await?;
        array(&value).map(Guild::from_value).collect()
    }

    pub async fn fetch_guild_channels(&self, guild_id: &str) -> Result<Vec<Channel>> {
        let value = self
            .call(
                Method::GET,
                &format!("/guilds/{guild_id}/channels"),
                &[],
                None,
            )
            .await?;
        array(&value)
            .map(|channel| Channel::from_value(channel, Some(guild_id), &[]))
            .collect()
    }

    pub async fn fetch_private_channels(&self) -> Result<Vec<Channel>> {
        let value = self
            .call(Method::GET, "/users/@me/channels", &[], None)
            .await?;
        array(&value)
            .map(|channel| Channel::from_value(channel, None, &[]))
            .collect()
    }

    /// Fetch a page of history in chronological order (oldest first).
    pub async fn fetch_messages(
        &self,
        channel_id: &str,
        before: Option<&str>,
        limit: u32,
    ) -> Result<Vec<Message>> {
        if !(1..=100).contains(&limit) {
            return Err(EngineError::InvalidInput(
                "limit must be between 1 and 100".into(),
            ));
        }
        let mut query = vec![("limit", limit.to_string())];
        if let Some(before) = before {
            query.push(("before", before.to_owned()));
        }
        let value = self
            .call(
                Method::GET,
                &format!("/channels/{channel_id}/messages"),
                &query,
                None,
            )
            .await?;
        let mut messages: Vec<Message> = array(&value)
            .map(Message::from_value)
            .collect::<Result<_>>()?;
        messages.sort_by_key(|message| message.id.parse::<u64>().unwrap_or(0));
        Ok(messages)
    }

    pub async fn send_message(
        &self,
        channel_id: &str,
        content: &str,
        reply_to: Option<MessageReference>,
        nonce: Option<String>,
    ) -> Result<Message> {
        if content.trim().is_empty() {
            return Err(EngineError::InvalidInput(
                "message content must not be empty".into(),
            ));
        }
        let mut body = json!({"content": content});
        if let Some(nonce) = nonce {
            body["nonce"] = json!(nonce);
        }
        if let Some(reference) = reply_to {
            body["message_reference"] = json!({
                "message_id": reference.message_id,
                "channel_id": reference.channel_id,
                "guild_id": reference.guild_id,
            });
        }
        let value = self
            .call(
                Method::POST,
                &format!("/channels/{channel_id}/messages"),
                &[],
                Some(&body),
            )
            .await?;
        Message::from_value(&value)
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.close();
    }
}

fn array(value: &Value) -> impl Iterator<Item = &Value> {
    value.as_array().into_iter().flatten()
}
