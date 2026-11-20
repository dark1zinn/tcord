use serde_json::Value;

/// Every failure the engine can report. Each variant maps to one Python exception class.
#[derive(thiserror::Error, Debug, Clone, PartialEq)]
pub enum EngineError {
    #[error("network error: {0}")]
    Network(String),
    #[error("not authenticated")]
    Unauthorized,
    #[error("missing permissions")]
    Forbidden { code: Option<u64> },
    #[error("not found")]
    NotFound,
    #[error("rate limited; retry after {retry_after}s")]
    RateLimited { retry_after: f64, global: bool },
    #[error("Discord requires a CAPTCHA")]
    CaptchaRequired {
        service: Option<String>,
        sitekey: Option<String>,
    },
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("HTTP {status}: {message}")]
    Http {
        status: u16,
        code: Option<u64>,
        message: String,
    },
    #[error("protocol error: {0}")]
    Protocol(String),
    #[error("credential store error: {0}")]
    CredentialStore(String),
    #[error("cancelled")]
    Cancelled,
}

pub type Result<T, E = EngineError> = std::result::Result<T, E>;

impl EngineError {
    /// Map a non-success Discord HTTP response to an error.
    pub fn from_response(status: u16, body: &Value) -> Self {
        let code = body.get("code").and_then(Value::as_u64);
        let message = body
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("request failed")
            .to_owned();
        match status {
            400 if body.get("captcha_key").is_some() => Self::CaptchaRequired {
                service: string_field(body, "captcha_service"),
                sitekey: string_field(body, "captcha_sitekey"),
            },
            401 => Self::Unauthorized,
            403 => Self::Forbidden { code },
            404 => Self::NotFound,
            429 => Self::RateLimited {
                retry_after: body
                    .get("retry_after")
                    .and_then(Value::as_f64)
                    .unwrap_or(1.0),
                global: body.get("global").and_then(Value::as_bool).unwrap_or(false),
            },
            _ => Self::Http {
                status,
                code,
                message,
            },
        }
    }
}

fn string_field(body: &Value, key: &str) -> Option<String> {
    body.get(key).and_then(Value::as_str).map(str::to_owned)
}
