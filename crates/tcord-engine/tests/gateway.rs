//! Gateway lifecycle against an in-process fake gateway speaking zlib-stream.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use flate2::{Compress, Compression, FlushCompress};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tcord_engine::gateway::{GatewayEvent, parse_ready};
use tcord_engine::{AuthFailure, AuthKind, Session, SessionConfig};
use tokio::net::{TcpListener, TcpStream};
use tokio_tungstenite::WebSocketStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[derive(Clone, Copy, PartialEq)]
enum Script {
    ReadyThenMessage,
    DropThenResume,
    InvalidSession,
    AuthFailed,
}

#[derive(Default)]
struct Log {
    paths: Vec<String>,
    payloads: Vec<(usize, Value)>,
}

type SharedLog = Arc<Mutex<Log>>;

struct Peer {
    socket: WebSocketStream<TcpStream>,
    zlib: Compress,
    connection: usize,
    log: SharedLog,
}

impl Peer {
    async fn send(&mut self, value: Value) {
        let input = value.to_string().into_bytes();
        let mut output = Vec::with_capacity(input.len() + 64);
        self.zlib
            .compress_vec(&input, &mut output, FlushCompress::Sync)
            .unwrap();
        // Split the frame in two to exercise buffering until the zlib suffix.
        let middle = output.len() / 2;
        self.socket
            .send(Message::Binary(output[..middle].to_vec()))
            .await
            .unwrap();
        self.socket
            .send(Message::Binary(output[middle..].to_vec()))
            .await
            .unwrap();
    }

    async fn dispatch(&mut self, seq: u64, kind: &str, data: Value) {
        self.send(json!({"op": 0, "s": seq, "t": kind, "d": data}))
            .await;
    }

    /// Next client payload with the given opcode (others are logged and skipped).
    async fn expect(&mut self, op: u64) -> Value {
        loop {
            let Some(Ok(Message::Text(text))) = self.socket.next().await else {
                panic!("client closed while waiting for op {op}");
            };
            let value: Value = serde_json::from_str(&text).unwrap();
            self.log
                .lock()
                .unwrap()
                .payloads
                .push((self.connection, value.clone()));
            if value["op"] == op {
                return value["d"].clone();
            }
        }
    }

    async fn close(&mut self, code: u16) {
        let frame = CloseFrame {
            code: CloseCode::from(code),
            reason: "".into(),
        };
        let _ = self.socket.send(Message::Close(Some(frame))).await;
    }
}

fn ready(url: &str) -> Value {
    json!({
        "v": 9,
        "user": {"id": "1", "username": "me"},
        "users": [{"id": "2", "username": "friend"}],
        "session_id": "S",
        "resume_gateway_url": url,
        "guilds": [{
            "id": "100",
            "properties": {"name": "Guild", "icon": null},
            "channels": [{"id": "101", "type": 0, "name": "general", "position": 0}],
            "roles": []
        }],
        "private_channels": [{"id": "200", "type": 1, "recipient_ids": ["2"], "last_message_id": "5"}],
        "read_state": {"entries": [{"id": "200", "last_message_id": "4", "mention_count": 1}], "partial": false, "version": 1}
    })
}

fn message(id: &str) -> Value {
    json!({
        "id": id, "channel_id": "101", "guild_id": "100",
        "author": {"id": "2", "username": "friend"},
        "content": "hi", "timestamp": "2026-01-01T00:00:00+00:00"
    })
}

async fn serve(stream: TcpStream, connection: usize, script: Script, url: String, log: SharedLog) {
    let path_log = Arc::clone(&log);
    #[allow(clippy::result_large_err)]
    let record = move |request: &Request, response: Response| {
        path_log
            .lock()
            .unwrap()
            .paths
            .push(request.uri().to_string());
        Ok(response)
    };
    let socket = tokio_tungstenite::accept_hdr_async(stream, record)
        .await
        .unwrap();
    let mut peer = Peer {
        socket,
        zlib: Compress::new(Compression::default(), true),
        connection,
        log,
    };
    peer.send(json!({"op": 10, "d": {"heartbeat_interval": 45000}}))
        .await;
    match (script, connection) {
        (Script::DropThenResume, 1) => {
            peer.expect(6).await;
            peer.dispatch(2, "RESUMED", json!({})).await;
            peer.dispatch(3, "MESSAGE_CREATE", message("10")).await;
        }
        (Script::InvalidSession, 1) => {
            peer.expect(2).await;
            peer.dispatch(1, "READY", ready(&url)).await;
        }
        _ => {
            peer.expect(2).await;
            peer.dispatch(1, "READY", ready(&url)).await;
            match script {
                Script::ReadyThenMessage => {
                    peer.dispatch(2, "MESSAGE_CREATE", message("10")).await;
                    peer.send(json!({"op": 1, "d": null})).await;
                    peer.expect(1).await;
                }
                Script::DropThenResume => return,
                Script::InvalidSession => {
                    peer.send(json!({"op": 9, "d": false})).await;
                }
                Script::AuthFailed => peer.close(4004).await,
            }
        }
    }
    // Keep the connection open until the client goes away.
    while let Some(Ok(_)) = peer.socket.next().await {}
}

async fn fake_gateway(script: Script) -> (String, SharedLog) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("ws://{}", listener.local_addr().unwrap());
    let log = SharedLog::default();
    let shared = Arc::clone(&log);
    let resume = base.clone();
    tokio::spawn(async move {
        let mut connection = 0;
        while let Ok((stream, _)) = listener.accept().await {
            tokio::spawn(serve(
                stream,
                connection,
                script,
                resume.clone(),
                Arc::clone(&shared),
            ));
            connection += 1;
        }
    });
    (
        format!("{base}/?v=9&encoding=json&compress=zlib-stream"),
        log,
    )
}

async fn session(gateway_url: String) -> (MockServer, Session) {
    let api = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"token": "tok"})))
        .mount(&api)
        .await;
    Mock::given(method("GET"))
        .and(path("/users/@me"))
        .respond_with(
            ResponseTemplate::new(200).set_body_json(json!({"id": "1", "username": "me"})),
        )
        .mount(&api)
        .await;
    let session = Session::new(SessionConfig {
        api_base: Some(api.uri()),
        gateway_url: Some(gateway_url),
        memory_store: true,
        reconnect_base: Some(Duration::from_millis(10)),
        ..SessionConfig::default()
    })
    .unwrap();
    session
        .auth()
        .login_password("a@b.test", "pw")
        .await
        .unwrap();
    (api, session)
}

/// Skip status events until the next interesting one.
async fn next_non_status(session: &Session) -> Option<GatewayEvent> {
    loop {
        let event = tokio::time::timeout(Duration::from_secs(10), session.next_event())
            .await
            .expect("timed out waiting for gateway event")?;
        if !matches!(event, GatewayEvent::Status { .. }) {
            return Some(event);
        }
    }
}

#[tokio::test]
async fn zlib_ready_then_message() {
    let (url, log) = fake_gateway(Script::ReadyThenMessage).await;
    let (_api, session) = session(url).await;

    let ready = session.connect_gateway().await.unwrap();

    assert_eq!(ready.user.username, "me");
    assert_eq!(ready.guilds[0].name, "Guild");
    assert_eq!(ready.guilds[0].channels[0].name.as_deref(), Some("general"));
    assert_eq!(ready.private_channels[0].recipients[0].username, "friend");
    assert_eq!(ready.read_states[0].mention_count, 1);
    match next_non_status(&session).await {
        Some(GatewayEvent::MessageCreate(message)) => assert_eq!(message.content, "hi"),
        other => panic!("unexpected {other:?}"),
    }
    tokio::time::sleep(Duration::from_millis(100)).await;
    let log = log.lock().unwrap();
    let identify = &log.payloads.iter().find(|(_, p)| p["op"] == 2).unwrap().1["d"];
    assert_eq!(identify["token"], "tok");
    assert_eq!(identify["capabilities"], 1047);
    assert_eq!(identify["properties"]["browser"], "Chrome");
    let heartbeat = log
        .payloads
        .iter()
        .rev()
        .find(|(_, p)| p["op"] == 1)
        .unwrap();
    assert_eq!(heartbeat.1["d"], 2);
}

#[tokio::test]
async fn resume_after_drop() {
    let (url, log) = fake_gateway(Script::DropThenResume).await;
    let (_api, session) = session(url).await;
    session.connect_gateway().await.unwrap();

    match next_non_status(&session).await {
        Some(GatewayEvent::MessageCreate(message)) => assert_eq!(message.id, "10"),
        other => panic!("unexpected {other:?}"),
    }
    let log = log.lock().unwrap();
    let resume = &log
        .payloads
        .iter()
        .find(|(connection, p)| *connection == 1 && p["op"] == 6)
        .unwrap()
        .1["d"];
    assert_eq!(resume["session_id"], "S");
    assert_eq!(resume["seq"], 1);
    assert!(
        log.paths[1].contains("compress=zlib-stream"),
        "{:?}",
        log.paths
    );
}

#[tokio::test]
async fn invalid_session_reidentifies() {
    let (url, log) = fake_gateway(Script::InvalidSession).await;
    let (_api, session) = session(url).await;
    session.connect_gateway().await.unwrap();

    match next_non_status(&session).await {
        Some(GatewayEvent::Ready(ready)) => assert_eq!(ready.user.id, "1"),
        other => panic!("unexpected {other:?}"),
    }
    let log = log.lock().unwrap();
    assert!(
        log.payloads
            .iter()
            .any(|(connection, p)| *connection == 1 && p["op"] == 2)
    );
    assert!(!log.payloads.iter().any(|(_, p)| p["op"] == 6));
}

#[tokio::test]
async fn auth_failed_close_logs_out() {
    let (url, _log) = fake_gateway(Script::AuthFailed).await;
    let (_api, session) = session(url).await;
    session.connect_gateway().await.unwrap();

    assert_eq!(
        next_non_status(&session).await,
        Some(GatewayEvent::SessionInvalidated)
    );
    let snapshot = session.auth().snapshot();
    assert_eq!(snapshot.kind, AuthKind::LoggedOut);
    assert_eq!(snapshot.failure, Some(AuthFailure::SessionExpired));
    assert_eq!(next_non_status(&session).await, None);
}

#[test]
fn legacy_ready_shape_parses_identically() {
    let modern = parse_ready(&ready("ws://x")).unwrap();
    let legacy = parse_ready(&json!({
        "user": {"id": "1", "username": "me"},
        "guilds": [{
            "id": "100", "name": "Guild", "icon": null,
            "channels": [{"id": "101", "type": 0, "name": "general", "position": 0}],
            "roles": []
        }],
        "private_channels": [{
            "id": "200", "type": 1, "last_message_id": "5",
            "recipients": [{"id": "2", "username": "friend"}]
        }],
        "read_state": [{"id": "200", "last_message_id": "4", "mention_count": 1}]
    }))
    .unwrap();
    assert_eq!(modern.guilds, legacy.guilds);
    assert_eq!(modern.private_channels, legacy.private_channels);
    assert_eq!(modern.read_states, legacy.read_states);
}
