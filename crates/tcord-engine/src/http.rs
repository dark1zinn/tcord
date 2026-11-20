use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::{Client, Method, Response};
use secrecy::{ExposeSecret, SecretString};
use serde_json::Value;
use tokio::time::{Instant, sleep, sleep_until};

use crate::error::{EngineError, Result};
use crate::identity;

pub const DEFAULT_API_BASE: &str = "https://discord.com/api/v9";

/// 429 waits up to this long are retried transparently (once); longer ones are reported.
const MAX_TRANSPARENT_RETRY: f64 = 5.0;
/// Self-imposed invalid-request budget, far below Discord's 10,000 / 10 min ban threshold.
const INVALID_REQUEST_LIMIT: usize = 2_000;
const INVALID_REQUEST_WINDOW: Duration = Duration::from_secs(600);
const GATEWAY_ERRORS: [u16; 5] = [502, 504, 522, 523, 524];
const GATEWAY_RETRY_DELAYS: [Duration; 2] = [Duration::from_millis(500), Duration::from_secs(1)];

/// Raw Discord response: status code plus parsed JSON body (`Null` when empty).
pub struct RawResponse {
    pub status: u16,
    pub body: Value,
}

#[derive(Default)]
struct Limits {
    /// Route key -> (remaining requests, instant the bucket resets).
    buckets: HashMap<String, (u64, Instant)>,
    global_until: Option<Instant>,
    invalid: VecDeque<Instant>,
}

/// Discord REST client with the web-client identity, bucket-aware rate limiting and an
/// invalid-request budget.
pub struct DiscordHttp {
    client: Client,
    base: String,
    limits: Mutex<Limits>,
}

impl DiscordHttp {
    pub fn new(base: Option<String>) -> Result<Self> {
        Self::with_locale(base, "en-US")
    }

    pub fn with_locale(base: Option<String>, locale: &str) -> Result<Self> {
        let mut headers = HeaderMap::new();
        let header = |value: String| {
            HeaderValue::from_str(&value)
                .map_err(|error| EngineError::InvalidInput(error.to_string()))
        };
        headers.insert(
            HeaderName::from_static("x-super-properties"),
            header(identity::encoded_super_properties(locale))?,
        );
        headers.insert(
            HeaderName::from_static("x-discord-locale"),
            header(locale.into())?,
        );
        headers.insert(reqwest::header::ACCEPT_LANGUAGE, header(locale.into())?);
        let client = Client::builder()
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(10))
            .user_agent(identity::user_agent())
            .default_headers(headers)
            .gzip(true)
            .build()
            .map_err(|error| EngineError::Network(error.to_string()))?;
        Ok(Self {
            client,
            base: base
                .unwrap_or_else(|| DEFAULT_API_BASE.into())
                .trim_end_matches('/')
                .to_owned(),
            limits: Mutex::new(Limits::default()),
        })
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    fn limits(&self) -> std::sync::MutexGuard<'_, Limits> {
        self.limits.lock().expect("rate limit state poisoned")
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
        let route = route_key(&method, path);
        let mut retried_429 = false;
        let mut gateway_retries = 0;
        loop {
            self.wait_for_capacity(&route).await?;
            let response = self.perform(&method, path, token, query, body).await?;
            let status = response.status().as_u16();
            let headers = response.headers().clone();
            let body = read_body(response).await?;
            self.record(&route, status, &headers, &body);

            if status == 429 && !retried_429 {
                let retry_after = body
                    .get("retry_after")
                    .and_then(Value::as_f64)
                    .unwrap_or(1.0);
                if retry_after <= MAX_TRANSPARENT_RETRY {
                    retried_429 = true;
                    sleep(Duration::from_secs_f64(retry_after)).await;
                    continue;
                }
            }
            if method == Method::GET
                && GATEWAY_ERRORS.contains(&status)
                && gateway_retries < GATEWAY_RETRY_DELAYS.len()
            {
                sleep(GATEWAY_RETRY_DELAYS[gateway_retries]).await;
                gateway_retries += 1;
                continue;
            }
            return Ok(RawResponse { status, body });
        }
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

    async fn perform(
        &self,
        method: &Method,
        path: &str,
        token: Option<&SecretString>,
        query: &[(&str, String)],
        body: Option<&Value>,
    ) -> Result<Response> {
        let mut request = self
            .client
            .request(method.clone(), format!("{}{}", self.base, path))
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
        request
            .send()
            .await
            .map_err(|error| EngineError::Network(error.without_url().to_string()))
    }

    /// Block until neither the global limit nor the route's bucket forbids a request.
    async fn wait_for_capacity(&self, route: &str) -> Result<()> {
        loop {
            let wait_until = {
                let mut limits = self.limits();
                let now = Instant::now();
                while limits
                    .invalid
                    .front()
                    .is_some_and(|at| now.duration_since(*at) > INVALID_REQUEST_WINDOW)
                {
                    limits.invalid.pop_front();
                }
                if limits.invalid.len() >= INVALID_REQUEST_LIMIT {
                    let oldest = limits.invalid[0];
                    let retry_after =
                        (INVALID_REQUEST_WINDOW - now.duration_since(oldest)).as_secs_f64();
                    return Err(EngineError::RateLimited {
                        retry_after,
                        global: true,
                    });
                }
                let global = limits.global_until.filter(|until| *until > now);
                let bucket = limits
                    .buckets
                    .get(route)
                    .filter(|(remaining, reset)| *remaining == 0 && *reset > now)
                    .map(|(_, reset)| *reset);
                global.into_iter().chain(bucket).max()
            };
            match wait_until {
                Some(until) => sleep_until(until).await,
                None => return Ok(()),
            }
        }
    }

    fn record(&self, route: &str, status: u16, headers: &HeaderMap, body: &Value) {
        let now = Instant::now();
        let mut limits = self.limits();
        let number = |name: &str| {
            headers
                .get(name)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<f64>().ok())
        };
        if let (Some(remaining), Some(reset_after)) = (
            number("x-ratelimit-remaining"),
            number("x-ratelimit-reset-after"),
        ) {
            limits.buckets.insert(
                route.to_owned(),
                (
                    remaining as u64,
                    now + Duration::from_secs_f64(reset_after.max(0.0)),
                ),
            );
        }
        if status == 429 && body.get("global").and_then(Value::as_bool) == Some(true) {
            let retry_after = body
                .get("retry_after")
                .and_then(Value::as_f64)
                .unwrap_or(1.0);
            limits.global_until = Some(now + Duration::from_secs_f64(retry_after.max(0.0)));
        }
        let shared = headers
            .get("x-ratelimit-scope")
            .is_some_and(|scope| scope == "shared");
        if matches!(status, 401 | 403) || (status == 429 && !shared) {
            limits.invalid.push_back(now);
        }
    }
}

async fn read_body(response: Response) -> Result<Value> {
    let bytes = response
        .bytes()
        .await
        .map_err(|error| EngineError::Network(error.without_url().to_string()))?;
    Ok(if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap_or(Value::Null)
    })
}

/// Rate-limit route: method plus path with every id except a leading channel/guild id replaced.
fn route_key(method: &Method, path: &str) -> String {
    let mut key = method.as_str().to_owned();
    let mut previous = "";
    for (index, segment) in path.split('/').enumerate() {
        key.push('/');
        let major = index == 2 && matches!(previous, "channels" | "guilds");
        if !major && !segment.is_empty() && segment.bytes().all(|byte| byte.is_ascii_digit()) {
            key.push_str(":id");
        } else {
            key.push_str(segment);
        }
        previous = segment;
    }
    key
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn route_keeps_major_parameter_only() {
        assert_eq!(
            route_key(&Method::GET, "/channels/123/messages/456"),
            "GET//channels/123/messages/:id"
        );
        assert_eq!(route_key(&Method::GET, "/users/@me"), "GET//users/@me");
    }
}
