//! Desktop remote-auth (QR code login), protocol v2.
//!
//! Reference: <https://docs.discord.food/remote-authentication/desktop>.

use std::time::{Duration, SystemTime};

use base64::Engine;
use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use futures_util::{SinkExt, StreamExt};
use reqwest::Method;
use rsa::PrivateKey;
use rsa::pkcs8::EncodePublicKey;
use rsa::sha2::{Digest, Sha256};
use secrecy::SecretString;
use serde_json::{Value, json};
use tokio::time::{Instant, MissedTickBehavior, interval_at, sleep_until, timeout};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::error::{EngineError, Result};
use crate::http::DiscordHttp;
use crate::models::QrUser;

/// How many times an expired QR code is replaced with a fresh one before giving up.
const MAX_RESTARTS: usize = 3;
const ORIGIN: &str = "https://discord.com";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const HELLO_TIMEOUT: Duration = Duration::from_secs(10);
/// Close code sent by the gateway when the QR session timed out.
const CLOSE_TIMEOUT: u16 = 4003;

/// Progress reported while a QR login runs.
pub(crate) enum QrEvent {
    Ready {
        url: String,
        expires_at: Option<SystemTime>,
    },
    Scanned(QrUser),
}

enum Attempt {
    Token(SecretString),
    Expired,
}

fn protocol(error: impl ToString) -> EngineError {
    EngineError::Protocol(error.to_string())
}

fn network(error: impl ToString) -> EngineError {
    EngineError::Network(error.to_string())
}

/// Run QR login until a token is obtained. Expired codes are replaced up to [`MAX_RESTARTS`]
/// times; the mobile user cancelling yields [`EngineError::Cancelled`].
pub(crate) async fn authenticate(
    url: &str,
    http: &DiscordHttp,
    on_event: &(dyn Fn(QrEvent) + Sync),
) -> Result<SecretString> {
    for _ in 0..=MAX_RESTARTS {
        match attempt(url, http, on_event).await? {
            Attempt::Token(token) => return Ok(token),
            Attempt::Expired => continue,
        }
    }
    Err(EngineError::Protocol("expired".into()))
}

async fn attempt(
    url: &str,
    http: &DiscordHttp,
    on_event: &(dyn Fn(QrEvent) + Sync),
) -> Result<Attempt> {
    let mut request = url.into_client_request().map_err(protocol)?;
    request
        .headers_mut()
        .insert("Origin", HeaderValue::from_static(ORIGIN));
    let (mut socket, _) = timeout(CONNECT_TIMEOUT, connect_async(request))
        .await
        .map_err(|_| network("remote auth connect timed out"))?
        .map_err(network)?;

    let hello = timeout(HELLO_TIMEOUT, socket.next())
        .await
        .map_err(|_| network("no hello from remote auth gateway"))?
        .ok_or_else(|| network("remote auth gateway closed"))?
        .map_err(network)?;
    let hello: Value =
        serde_json::from_str(hello.to_text().map_err(protocol)?).map_err(protocol)?;
    if hello.get("op").and_then(Value::as_str) != Some("hello") {
        return Err(protocol("expected hello"));
    }
    let heartbeat_ms = hello
        .get("heartbeat_interval")
        .and_then(Value::as_u64)
        .ok_or_else(|| protocol("hello without heartbeat_interval"))?;
    let timeout_ms = hello
        .get("timeout_ms")
        .and_then(Value::as_u64)
        .unwrap_or(120_000);
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    let expires_at = SystemTime::now() + Duration::from_millis(timeout_ms);

    let key = tokio::task::spawn_blocking(|| PrivateKey::new(&mut getrandom::SysRng, 2048))
        .await
        .map_err(protocol)?
        .map_err(protocol)?;
    let spki = key.to_public_key().to_public_key_der().map_err(protocol)?;
    let spki = spki.as_bytes();
    send(
        &mut socket,
        json!({"op": "init", "encoded_public_key": STANDARD.encode(spki)}),
    )
    .await?;
    let expected_fingerprint = URL_SAFE_NO_PAD.encode(Sha256::digest(spki));

    let period = Duration::from_millis(heartbeat_ms.max(1));
    let mut heartbeat = interval_at(Instant::now() + jitter(period), period);
    heartbeat.set_missed_tick_behavior(MissedTickBehavior::Delay);
    let mut acked = true;

    loop {
        tokio::select! {
            _ = sleep_until(deadline) => return Ok(Attempt::Expired),
            _ = heartbeat.tick() => {
                if !acked {
                    return Err(network("remote auth heartbeat not acknowledged"));
                }
                acked = false;
                send(&mut socket, json!({"op": "heartbeat"})).await?;
            }
            message = socket.next() => {
                let message = match message {
                    Some(Ok(message)) => message,
                    Some(Err(error)) => return Err(network(error)),
                    None => return Err(network("remote auth gateway closed")),
                };
                let text = match message {
                    Message::Text(text) => text,
                    Message::Close(frame) => {
                        let code = frame.map(|frame| u16::from(frame.code));
                        if code == Some(CLOSE_TIMEOUT) {
                            return Ok(Attempt::Expired);
                        }
                        return Err(network(format!("remote auth closed ({code:?})")));
                    }
                    _ => continue,
                };
                let payload: Value = serde_json::from_str(&text).map_err(protocol)?;
                match payload.get("op").and_then(Value::as_str) {
                    Some("heartbeat_ack") => acked = true,
                    Some("nonce_proof") => {
                        let nonce = decrypt(&key, &payload, "encrypted_nonce")?;
                        send(
                            &mut socket,
                            json!({"op": "nonce_proof", "nonce": URL_SAFE_NO_PAD.encode(nonce)}),
                        )
                        .await?;
                    }
                    Some("pending_remote_init") => {
                        let fingerprint = field(&payload, "fingerprint")?;
                        if fingerprint != expected_fingerprint {
                            let _ = socket.close(None).await;
                            return Err(protocol("remote auth fingerprint mismatch"));
                        }
                        on_event(QrEvent::Ready {
                            url: format!("https://discord.com/ra/{fingerprint}"),
                            expires_at: Some(expires_at),
                        });
                    }
                    Some("pending_ticket") => {
                        let raw = String::from_utf8(decrypt(&key, &payload, "encrypted_user_payload")?)
                            .map_err(protocol)?;
                        on_event(QrEvent::Scanned(parse_user(&raw)?));
                    }
                    Some("pending_login") => {
                        let ticket = field(&payload, "ticket")?.to_owned();
                        let _ = socket.close(None).await;
                        return exchange_ticket(http, &key, &ticket).await.map(Attempt::Token);
                    }
                    Some("cancel") => {
                        let _ = socket.close(None).await;
                        return Err(EngineError::Cancelled);
                    }
                    _ => {}
                }
            }
        }
    }
}

async fn exchange_ticket(
    http: &DiscordHttp,
    key: &PrivateKey,
    ticket: &str,
) -> Result<SecretString> {
    let body = json!({"ticket": ticket});
    let response = http
        .send(
            Method::POST,
            "/users/@me/remote-auth/login",
            None,
            &[],
            Some(&body),
        )
        .await?;
    let token = String::from_utf8(decrypt(key, &response, "encrypted_token")?).map_err(protocol)?;
    Ok(SecretString::from(token))
}

async fn send<S>(socket: &mut S, payload: Value) -> Result<()>
where
    S: SinkExt<Message> + Unpin,
    S::Error: ToString,
{
    socket
        .send(Message::Text(payload.to_string()))
        .await
        .map_err(network)
}

fn field<'a>(payload: &'a Value, key: &str) -> Result<&'a str> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| protocol(format!("missing {key}")))
}

fn decrypt(key: &PrivateKey, payload: &Value, name: &str) -> Result<Vec<u8>> {
    let bytes = STANDARD.decode(field(payload, name)?).map_err(protocol)?;
    key.oaep_decrypt::<Sha256>(&bytes).map_err(protocol)
}

/// Random delay in `[0, period)` for the first heartbeat.
fn jitter(period: Duration) -> Duration {
    let mut bytes = [0u8; 8];
    if getrandom::fill(&mut bytes).is_err() {
        return Duration::ZERO;
    }
    let fraction = (u64::from_le_bytes(bytes) % 1_000_000) as f64 / 1_000_000.0;
    period.mul_f64(fraction)
}

/// Parse the `id:discriminator:avatar:username` payload of a scanned QR code.
fn parse_user(raw: &str) -> Result<QrUser> {
    let mut parts = raw.splitn(4, ':');
    let (Some(id), Some(discriminator), Some(avatar), Some(username)) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return Err(protocol("malformed user payload"));
    };
    Ok(QrUser {
        id: id.into(),
        discriminator: discriminator.into(),
        avatar: (avatar != "0").then(|| avatar.into()),
        username: username.into(),
    })
}
