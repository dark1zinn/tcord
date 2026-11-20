use std::sync::{Arc, Mutex};

use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use rsa::{
    PrivateKey,
    pkcs8::{EncodePublicKey, LineEnding},
    sha2::{Digest, Sha256},
};
use serde_json::{Value, json};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::{AuthStatus, SessionData, set_status};

pub(crate) async fn authenticate(data: Arc<Mutex<SessionData>>) -> Result<String, ()> {
    let (mut socket, _) = connect_async("wss://remote-auth-gateway.discord.gg/?v=2")
        .await
        .map_err(|_| ())?;
    let private_key = PrivateKey::new(&mut getrandom::SysRng, 2048).map_err(|_| ())?;
    let public_key = private_key
        .to_public_key()
        .to_public_key_pem(LineEnding::LF)
        .map_err(|_| ())?;
    socket
        .send(Message::Text(
            json!({"op":"init","encoded_public_key":public_key}).to_string(),
        ))
        .await
        .map_err(|_| ())?;
    while let Some(message) = socket.next().await {
        let message = message.map_err(|_| ())?;
        let payload: Value =
            serde_json::from_str(message.to_text().map_err(|_| ())?).map_err(|_| ())?;
        match payload.get("op").and_then(Value::as_str) {
            Some("pending_remote_init") => {
                let fingerprint = payload
                    .get("fingerprint")
                    .and_then(Value::as_str)
                    .ok_or(())?;
                set_status(
                    &data,
                    AuthStatus::WaitingForQr {
                        payload: format!("https://discord.com/ra/{fingerprint}"),
                        expires_at: "five minutes".into(),
                    },
                );
            }
            Some("nonce_proof") => {
                let nonce = decrypt(&private_key, &payload, "encrypted_nonce")?;
                socket
                    .send(Message::Text(
                        json!({"op":"nonce_proof","proof":STANDARD.encode(Sha256::digest(nonce))})
                            .to_string(),
                    ))
                    .await
                    .map_err(|_| ())?;
            }
            Some("pending_ticket") => {
                let ticket =
                    String::from_utf8(decrypt(&private_key, &payload, "encrypted_user_payload")?)
                        .map_err(|_| ())?;
                socket
                    .send(Message::Text(
                        json!({"op":"remote_auth_login","ticket":ticket}).to_string(),
                    ))
                    .await
                    .map_err(|_| ())?;
            }
            Some("finish") => {
                return String::from_utf8(decrypt(&private_key, &payload, "encrypted_token")?)
                    .map_err(|_| ());
            }
            _ => {}
        }
    }
    Err(())
}

fn decrypt(key: &PrivateKey, payload: &Value, field: &str) -> Result<Vec<u8>, ()> {
    let encoded = payload.get(field).and_then(Value::as_str).ok_or(())?;
    let bytes = STANDARD.decode(encoded).map_err(|_| ())?;
    key.oaep_decrypt::<Sha256>(&bytes).map_err(|_| ())
}
