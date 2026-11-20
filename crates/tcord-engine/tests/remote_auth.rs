//! QR login against an in-process fake remote-auth gateway.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use futures_util::{SinkExt, StreamExt};
use rsa::PublicKey;
use rsa::pkcs8::DecodePublicKey;
use rsa::sha2::{Digest, Sha256};
use serde_json::{Value, json};
use tcord_engine::{AuthController, AuthFailure, AuthKind, DiscordHttp, MemoryStore};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request as HttpRequest, Respond, ResponseTemplate};

const NONCE: &[u8] = b"proof-of-key";

#[derive(Clone, Copy)]
enum Script {
    /// Full handshake ending in `pending_login`.
    Login,
    /// Handshake then the phone cancels.
    Cancel,
    /// Close with 4003 right after `hello`.
    Expire,
    /// Handshake then stay silent forever.
    Stall,
}

type SharedKey = Arc<Mutex<Option<PublicKey>>>;

struct Gateway {
    url: String,
    connections: Arc<Mutex<usize>>,
}

async fn gateway(script: Script, key: SharedKey) -> Gateway {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/?v=2", listener.local_addr().unwrap());
    let connections = Arc::new(Mutex::new(0));
    let counter = Arc::clone(&connections);
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            *counter.lock().unwrap() += 1;
            let key = Arc::clone(&key);
            tokio::spawn(async move {
                // The error type is dictated by tungstenite's handshake callback signature.
                #[allow(clippy::result_large_err)]
                let check_origin = |request: &Request, response: Response| {
                    assert_eq!(request.headers()["origin"], "https://discord.com");
                    Ok(response)
                };
                let socket = tokio_tungstenite::accept_hdr_async(stream, check_origin)
                    .await
                    .unwrap();
                serve(socket, script, key).await;
            });
        }
    });
    Gateway { url, connections }
}

async fn serve<S>(mut socket: tokio_tungstenite::WebSocketStream<S>, script: Script, key: SharedKey)
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let send = |value: Value| Message::Text(value.to_string());
    socket
        .send(send(
            json!({"op": "hello", "heartbeat_interval": 41250, "timeout_ms": 120000}),
        ))
        .await
        .unwrap();
    if let Script::Expire = script {
        let frame = CloseFrame {
            code: CloseCode::from(4003),
            reason: "timeout".into(),
        };
        let _ = socket.send(Message::Close(Some(frame))).await;
        return;
    }
    let init: Value = next_json(&mut socket).await;
    assert_eq!(init["op"], "init");
    let spki = STANDARD
        .decode(init["encoded_public_key"].as_str().unwrap())
        .unwrap();
    let public = PublicKey::from_public_key_der(&spki).unwrap();
    *key.lock().unwrap() = Some(public.clone());
    let encrypted = public.oaep_encrypt::<Sha256>(NONCE).unwrap();
    socket
        .send(send(
            json!({"op": "nonce_proof", "encrypted_nonce": STANDARD.encode(encrypted)}),
        ))
        .await
        .unwrap();
    let proof: Value = next_json(&mut socket).await;
    assert_eq!(proof["op"], "nonce_proof");
    assert_eq!(proof["nonce"], URL_SAFE_NO_PAD.encode(NONCE));
    let fingerprint = URL_SAFE_NO_PAD.encode(Sha256::digest(&spki));
    socket
        .send(send(
            json!({"op": "pending_remote_init", "fingerprint": fingerprint}),
        ))
        .await
        .unwrap();
    let user = public
        .oaep_encrypt::<Sha256>(b"196769986071625728:0:0:mary")
        .unwrap();
    socket
        .send(send(
            json!({"op": "pending_ticket", "encrypted_user_payload": STANDARD.encode(user)}),
        ))
        .await
        .unwrap();
    match script {
        Script::Login => {
            socket
                .send(send(json!({"op": "pending_login", "ticket": "TICKET"})))
                .await
                .unwrap();
        }
        Script::Cancel => {
            socket.send(send(json!({"op": "cancel"}))).await.unwrap();
        }
        Script::Stall => tokio::time::sleep(Duration::from_secs(60)).await,
        Script::Expire => unreachable!(),
    }
}

async fn next_json<S>(socket: &mut tokio_tungstenite::WebSocketStream<S>) -> Value
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    loop {
        match socket.next().await.unwrap().unwrap() {
            Message::Text(text) => {
                let value: Value = serde_json::from_str(&text).unwrap();
                if value["op"] != "heartbeat" {
                    return value;
                }
            }
            _ => continue,
        }
    }
}

/// Answers the ticket exchange with a token encrypted for the client's key.
struct TicketExchange(SharedKey);

impl Respond for TicketExchange {
    fn respond(&self, request: &HttpRequest) -> ResponseTemplate {
        let body: Value = serde_json::from_slice(&request.body).unwrap();
        assert_eq!(body["ticket"], "TICKET");
        let key = self.0.lock().unwrap().clone().unwrap();
        let token = key.oaep_encrypt::<Sha256>(b"qr-token").unwrap();
        ResponseTemplate::new(200).set_body_json(json!({"encrypted_token": STANDARD.encode(token)}))
    }
}

async fn api(key: SharedKey) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/users/@me/remote-auth/login"))
        .respond_with(TicketExchange(key))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/users/@me"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({"id": "196769986071625728", "username": "mary"})),
        )
        .mount(&server)
        .await;
    server
}

async fn controller(api: &MockServer, gateway: &Gateway) -> Arc<AuthController> {
    let http = Arc::new(DiscordHttp::new(Some(api.uri())).unwrap());
    Arc::new(AuthController::new(
        http,
        Arc::new(MemoryStore::default()),
        Some(gateway.url.clone()),
    ))
}

#[tokio::test]
async fn full_handshake_authenticates() {
    let key = SharedKey::default();
    let gateway = gateway(Script::Login, Arc::clone(&key)).await;
    let api = api(key).await;
    let auth = controller(&api, &gateway).await;
    let mut updates = auth.subscribe();
    let seen = tokio::spawn(async move {
        let mut seen = (None, None);
        while updates.changed().await.is_ok() {
            let snapshot = updates.borrow().clone();
            if let Some(url) = snapshot.qr_url {
                seen.0 = Some(url);
            }
            if let Some(user) = snapshot.qr_user {
                seen.1 = Some(user.username);
            }
            if snapshot.kind == AuthKind::Authenticated {
                break;
            }
        }
        seen
    });

    let snapshot = auth.start_qr().await;

    assert_eq!(snapshot.kind, AuthKind::Authenticated);
    assert_eq!(snapshot.user.unwrap().username, "mary");
    assert_eq!(auth.token().map(|_| ()), Some(()));
    let (url, user) = seen.await.unwrap();
    let url = url.expect("QR url published");
    assert!(url.starts_with("https://discord.com/ra/"), "{url}");
    assert_eq!(user.as_deref(), Some("mary"));
}

#[tokio::test]
async fn phone_cancel_reports_cancelled() {
    let key = SharedKey::default();
    let gateway = gateway(Script::Cancel, Arc::clone(&key)).await;
    let api = api(key).await;
    let auth = controller(&api, &gateway).await;

    let snapshot = auth.start_qr().await;

    assert_eq!(snapshot.kind, AuthKind::Failed);
    assert_eq!(snapshot.failure, Some(AuthFailure::QrCancelled));
}

#[tokio::test]
async fn timeout_four_times_expires() {
    let key = SharedKey::default();
    let gateway = gateway(Script::Expire, Arc::clone(&key)).await;
    let api = api(key).await;
    let auth = controller(&api, &gateway).await;

    let snapshot = auth.start_qr().await;

    assert_eq!(snapshot.kind, AuthKind::Failed);
    assert_eq!(snapshot.failure, Some(AuthFailure::QrExpired));
    assert_eq!(*gateway.connections.lock().unwrap(), 4);
}

#[tokio::test]
async fn logout_mid_handshake_stays_logged_out() {
    let key = SharedKey::default();
    let gateway = gateway(Script::Stall, Arc::clone(&key)).await;
    let api = api(key).await;
    let auth = controller(&api, &gateway).await;
    let running = tokio::spawn({
        let auth = Arc::clone(&auth);
        async move { auth.start_qr().await }
    });
    let mut updates = auth.subscribe();
    updates
        .wait_for(|snapshot| snapshot.qr_user.is_some())
        .await
        .unwrap();

    auth.logout().await;
    running.await.unwrap();

    assert_eq!(auth.snapshot().kind, AuthKind::LoggedOut);
    assert!(auth.token().is_none());
}
