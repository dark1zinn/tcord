use std::time::SystemTime;

use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use rsa::{
    PrivateKey,
    pkcs8::{EncodePublicKey, LineEnding},
    sha2::{Digest, Sha256},
};
use secrecy::SecretString;
use serde_json::{Value, json};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::error::{EngineError, Result};
use crate::http::DiscordHttp;
use crate::models::QrUser;

/// Progress reported while a QR login runs.
pub(crate) enum QrEvent {
    Ready {
        url: String,
        expires_at: Option<SystemTime>,
    },
    Scanned(QrUser),
}

fn protocol(error: impl ToString) -> EngineError {
    EngineError::Protocol(error.to_string())
}

pub(crate) async fn authenticate(
    url: &str,
    _http: &DiscordHttp,
    on_event: &(dyn Fn(QrEvent) + Sync),
) -> Result<SecretString> {
    let (mut socket, _) = connect_async(url).await.map_err(protocol)?;
    let private_key = PrivateKey::new(&mut getrandom::SysRng, 2048).map_err(protocol)?;
    let public_key = private_key
        .to_public_key()
        .to_public_key_pem(LineEnding::LF)
        .map_err(protocol)?;
    socket
        .send(Message::Text(
            json!({"op":"init","encoded_public_key":public_key}).to_string(),
        ))
        .await
        .map_err(protocol)?;
    while let Some(message) = socket.next().await {
        let message = message.map_err(protocol)?;
        let payload: Value =
            serde_json::from_str(message.to_text().map_err(protocol)?).map_err(protocol)?;
        match payload.get("op").and_then(Value::as_str) {
            Some("pending_remote_init") => {
                let fingerprint = payload
                    .get("fingerprint")
                    .and_then(Value::as_str)
                    .ok_or_else(|| protocol("missing fingerprint"))?;
                on_event(QrEvent::Ready {
                    url: format!("https://discord.com/ra/{fingerprint}"),
                    expires_at: None,
                });
            }
            Some("nonce_proof") => {
                let nonce = decrypt(&private_key, &payload, "encrypted_nonce")?;
                socket
                    .send(Message::Text(
                        json!({"op":"nonce_proof","proof":STANDARD.encode(Sha256::digest(nonce))})
                            .to_string(),
                    ))
                    .await
                    .map_err(protocol)?;
            }
            Some("pending_ticket") => {
                let raw =
                    String::from_utf8(decrypt(&private_key, &payload, "encrypted_user_payload")?)
                        .map_err(protocol)?;
                on_event(QrEvent::Scanned(parse_user(&raw)?));
            }
            Some("finish") => {
                let token = String::from_utf8(decrypt(&private_key, &payload, "encrypted_token")?)
                    .map_err(protocol)?;
                return Ok(SecretString::from(token));
            }
            _ => {}
        }
    }
    Err(protocol("remote auth gateway closed"))
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

fn decrypt(key: &PrivateKey, payload: &Value, field: &str) -> Result<Vec<u8>> {
    let encoded = payload
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| protocol(format!("missing {field}")))?;
    let bytes = STANDARD.decode(encoded).map_err(protocol)?;
    key.oaep_decrypt::<Sha256>(&bytes).map_err(protocol)
}
