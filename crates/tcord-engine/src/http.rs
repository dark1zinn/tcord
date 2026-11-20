use std::time::Duration;

use reqwest::{Client, Method, header::HeaderValue};
use secrecy::{ExposeSecret, SecretString};
use serde_json::Value;

use crate::error::{EngineError, Result};

pub const DEFAULT_API_BASE: &str = "https://discord.com/api/v9";

/// Raw Discord response: status code plus parsed JSON body (`Null` when empty).
pub struct RawResponse {
    pub status: u16,
    pub body: Value,
}

/// Thin Discord REST client. Rate limiting and client identity are layered on in `send_raw`.
pub struct DiscordHttp {
    client: Client,
    base: String,
}

impl DiscordHttp {
    pub fn new(base: Option<String>) -> Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(10))
            .user_agent("Tcord/0.1")
            .build()
            .map_err(|error| EngineError::Network(error.to_string()))?;
        Ok(Self {
            client,
            base: base
                .unwrap_or_else(|| DEFAULT_API_BASE.into())
                .trim_end_matches('/')
                .to_owned(),
        })
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    /// Perform a request and return the status and body without interpreting the status.
    pub async fn send_raw(
        &self,
        method: Method,
        path: &str,
        token: Option<&SecretString>,
        query: &[(&str, String)],
        body: Option<&Value>,
    ) -> Result<RawResponse> {
        let mut request = self
            .client
            .request(method, format!("{}{}", self.base, path))
            .query(query);
        if let Some(token) = token {
            let mut value = HeaderValue::from_str(token.expose_secret())
                .map_err(|_| EngineError::InvalidInput("malformed token".into()))?;
            value.set_sensitive(true);
            request = request.header(reqwest::header::AUTHORIZATION, value);
        }
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request
            .send()
            .await
            .map_err(|error| EngineError::Network(error.without_url().to_string()))?;
        let status = response.status().as_u16();
        let bytes = response
            .bytes()
            .await
            .map_err(|error| EngineError::Network(error.without_url().to_string()))?;
        let body = if bytes.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&bytes).unwrap_or(Value::Null)
        };
        Ok(RawResponse { status, body })
    }

    /// Perform a request and map non-2xx statuses to [`EngineError`].
    pub async fn send(
        &self,
        method: Method,
        path: &str,
        token: Option<&SecretString>,
        query: &[(&str, String)],
        body: Option<&Value>,
    ) -> Result<Value> {
        let response = self.send_raw(method, path, token, query, body).await?;
        if (200..300).contains(&response.status) {
            Ok(response.body)
        } else {
            Err(EngineError::from_response(response.status, &response.body))
        }
    }
}
