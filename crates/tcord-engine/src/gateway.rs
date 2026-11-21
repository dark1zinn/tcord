//! Discord user gateway: zlib-stream transport, heartbeats, identify/resume and the
//! dispatch events needed for text chat.
//!
//! Reference: <https://docs.discord.food/gateway/using-gateway>.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use flate2::{Decompress, FlushDecompress, Status};
use futures_util::{SinkExt, StreamExt};
use secrecy::{ExposeSecret, SecretString};
use serde::Deserialize;
use serde_json::value::RawValue;
use serde_json::{Value, json};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot};
use tokio::time::{Instant, MissedTickBehavior, interval_at, sleep, timeout};
use tokio_tungstenite::tungstenite::Message as WsMessage;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async};
use tokio_util::sync::CancellationToken;

use crate::auth::AuthController;
use crate::discord::{Channel, Guild, Member, Message, Role};
use crate::error::{EngineError, Result};
use crate::identity;
use crate::models::{AuthKind, User};

pub const DEFAULT_GATEWAY_URL: &str =
    "wss://gateway.discord.gg/?v=9&encoding=json&compress=zlib-stream";

/// LAZY_USER_NOTES | NO_AFFINE_USER_IDS | VERSIONED_READ_STATES | DEDUPE_USER_OBJECTS |
/// CLIENT_STATE_V2.
const CAPABILITIES: u64 = 1 | 1 << 1 | 1 << 2 | 1 << 4 | 1 << 10;
const EVENT_BUFFER: usize = 4096;
const MAX_BACKOFF_STEPS: u32 = 6;
const ZLIB_SUFFIX: [u8; 4] = [0, 0, 0xff, 0xff];

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[derive(Clone, Debug, PartialEq)]
pub struct ReadState {
    pub channel_id: String,
    pub last_message_id: Option<String>,
    pub mention_count: u32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Ready {
    pub user: User,
    pub guilds: Vec<Guild>,
    pub private_channels: Vec<Channel>,
    pub read_states: Vec<ReadState>,
    pub users: Vec<User>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConnectionStatus {
    Connecting,
    Connected,
    Resuming,
    Reconnecting,
    Disconnected,
}

impl ConnectionStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Connecting => "connecting",
            Self::Connected => "connected",
            Self::Resuming => "resuming",
            Self::Reconnecting => "reconnecting",
            Self::Disconnected => "disconnected",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum GatewayEvent {
    Ready(Box<Ready>),
    MessageCreate(Box<Message>),
    MessageUpdate(Box<Message>),
    MessageDelete {
        id: String,
        channel_id: String,
        guild_id: Option<String>,
    },
    TypingStart {
        channel_id: String,
        guild_id: Option<String>,
        user_id: String,
        timestamp: i64,
    },
    MessageAck {
        channel_id: String,
        message_id: String,
        mention_count: Option<u32>,
    },
    ChannelUpsert(Box<Channel>),
    ChannelDelete {
        id: String,
        guild_id: Option<String>,
    },
    GuildUpsert(Box<Guild>),
    GuildDelete {
        id: String,
        unavailable: bool,
    },
    Status {
        status: ConnectionStatus,
        retry_in: Option<f64>,
    },
    SessionInvalidated,
    ThreadUpsert(Box<Channel>),
    ThreadDelete {
        id: String,
        guild_id: Option<String>,
        parent_id: Option<String>,
    },
    /// The signed-in user joined (`true`) or left (`false`) a thread.
    ThreadMembership {
        thread_id: String,
        guild_id: Option<String>,
        joined: bool,
    },
    /// Replaces the known active threads of the listed parents (or the whole guild).
    ThreadListSync {
        guild_id: String,
        channel_ids: Option<Vec<String>>,
        threads: Vec<Channel>,
    },
    /// Members from GUILD_MEMBER_ADD/UPDATE and GUILD_MEMBERS_CHUNK.
    Members {
        guild_id: String,
        members: Vec<Member>,
    },
    RoleUpsert {
        guild_id: String,
        role: Role,
    },
    RoleDelete {
        guild_id: String,
        role_id: String,
    },
}

#[derive(Clone, Debug)]
pub struct GatewayConfig {
    pub url: String,
    pub locale: String,
    /// Base unit for reconnect backoff and identify pacing (1 s in production).
    pub reconnect_base: Duration,
}

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            url: DEFAULT_GATEWAY_URL.into(),
            locale: "en-US".into(),
            reconnect_base: Duration::from_secs(1),
        }
    }
}

enum Command {
    Subscribe(String),
    RequestMembers {
        guild_id: String,
        user_ids: Vec<String>,
    },
}

/// A running gateway connection owned by a session.
pub struct GatewayHandle {
    commands: mpsc::UnboundedSender<Command>,
    events: tokio::sync::Mutex<mpsc::Receiver<GatewayEvent>>,
    cancel: CancellationToken,
}

impl GatewayHandle {
    /// Connect and wait for the first READY.
    pub async fn connect(
        config: GatewayConfig,
        auth: Arc<AuthController>,
    ) -> Result<(Self, Ready)> {
        let token = auth.token().ok_or(EngineError::Unauthorized)?;
        let (events_tx, events_rx) = mpsc::channel(EVENT_BUFFER);
        let (commands_tx, commands_rx) = mpsc::unbounded_channel();
        let (ready_tx, ready_rx) = oneshot::channel();
        let cancel = CancellationToken::new();
        let runner = Runner {
            config,
            auth,
            token,
            events: events_tx,
            commands: commands_rx,
            cancel: cancel.clone(),
            ready: Some(ready_tx),
            session_id: None,
            user_id: None,
            resume_url: None,
            seq: None,
            subscribed: HashSet::new(),
            last_identify: None,
        };
        tokio::spawn(runner.run());
        let ready = ready_rx.await.map_err(|_| {
            cancel.cancel();
            EngineError::Network("gateway closed before READY".into())
        })??;
        Ok((
            Self {
                commands: commands_tx,
                events: tokio::sync::Mutex::new(events_rx),
                cancel,
            },
            ready,
        ))
    }

    /// Next event, or `None` once the gateway has stopped.
    pub async fn next_event(&self) -> Option<GatewayEvent> {
        self.events.lock().await.recv().await
    }

    pub fn subscribe_guild(&self, guild_id: String) {
        let _ = self.commands.send(Command::Subscribe(guild_id));
    }

    /// Ask for the members of `user_ids` (op 8); they arrive as [`GatewayEvent::Members`].
    pub fn request_members(&self, guild_id: String, user_ids: Vec<String>) {
        if !user_ids.is_empty() {
            let _ = self
                .commands
                .send(Command::RequestMembers { guild_id, user_ids });
        }
    }

    pub fn disconnect(&self) {
        self.cancel.cancel();
    }
}

impl Drop for GatewayHandle {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}

/// Why a single connection ended.
enum Outcome {
    Stopped,
    Resume,
    Reidentify,
    AuthFailed,
    Fatal,
}

struct Runner {
    config: GatewayConfig,
    auth: Arc<AuthController>,
    token: SecretString,
    events: mpsc::Sender<GatewayEvent>,
    commands: mpsc::UnboundedReceiver<Command>,
    cancel: CancellationToken,
    ready: Option<oneshot::Sender<Result<Ready>>>,
    session_id: Option<String>,
    /// The signed-in user, to pick our own entries out of membership updates.
    user_id: Option<String>,
    resume_url: Option<String>,
    seq: Option<u64>,
    subscribed: HashSet<String>,
    last_identify: Option<Instant>,
}

impl Runner {
    async fn run(mut self) {
        let mut failures: u32 = 0;
        loop {
            let resuming = self.session_id.is_some();
            self.status(
                if resuming {
                    ConnectionStatus::Resuming
                } else {
                    ConnectionStatus::Connecting
                },
                None,
            )
            .await;
            let outcome = match self.connect().await {
                Ok(socket) => self.drive(socket, &mut failures).await,
                Err(_) => Outcome::Resume,
            };
            match outcome {
                Outcome::Stopped => break,
                Outcome::Fatal => {
                    self.fail_ready("gateway rejected the session");
                    self.status(ConnectionStatus::Disconnected, None).await;
                    break;
                }
                Outcome::AuthFailed => {
                    self.fail_ready("authentication failed");
                    self.auth.invalidate_if_current(&self.token);
                    let _ = self.events.send(GatewayEvent::SessionInvalidated).await;
                    break;
                }
                Outcome::Reidentify => {
                    self.session_id = None;
                    self.seq = None;
                    self.resume_url = None;
                }
                Outcome::Resume => {}
            }
            if self.cancel.is_cancelled() || !self.authenticated() {
                break;
            }
            failures += 1;
            let delay = self.backoff(failures);
            self.status(ConnectionStatus::Reconnecting, Some(delay.as_secs_f64()))
                .await;
            tokio::select! {
                _ = self.cancel.cancelled() => break,
                _ = sleep(delay) => {}
            }
        }
        self.fail_ready("gateway stopped");
    }

    fn authenticated(&self) -> bool {
        self.auth.snapshot().kind == AuthKind::Authenticated
    }

    fn fail_ready(&mut self, reason: &str) {
        if let Some(ready) = self.ready.take() {
            let _ = ready.send(Err(EngineError::Network(reason.into())));
        }
    }

    fn backoff(&self, failures: u32) -> Duration {
        let steps = failures.saturating_sub(1).min(MAX_BACKOFF_STEPS);
        let delay = self.config.reconnect_base * 2u32.pow(steps);
        let cap = self.config.reconnect_base * 60;
        delay.min(cap) + self.config.reconnect_base.mul_f64(random_fraction())
    }

    async fn status(&self, status: ConnectionStatus, retry_in: Option<f64>) {
        let _ = self
            .events
            .send(GatewayEvent::Status { status, retry_in })
            .await;
    }

    async fn connect(&self) -> Result<Socket> {
        let url = match &self.resume_url {
            Some(resume) if self.session_id.is_some() => with_query(resume, &self.config.url),
            _ => self.config.url.clone(),
        };
        let (socket, _) = timeout(Duration::from_secs(15), connect_async(url.as_str()))
            .await
            .map_err(|_| EngineError::Network("gateway connect timed out".into()))?
            .map_err(|error| EngineError::Network(error.to_string()))?;
        Ok(socket)
    }

    async fn drive(&mut self, mut socket: Socket, failures: &mut u32) -> Outcome {
        let mut inflater = Inflater::new();
        let mut heartbeat: Option<tokio::time::Interval> = None;
        let mut acked = true;
        let mut auth_changes = self.auth.subscribe();
        loop {
            let tick = async {
                match heartbeat.as_mut() {
                    Some(interval) => {
                        interval.tick().await;
                    }
                    None => std::future::pending().await,
                }
            };
            tokio::select! {
                _ = self.cancel.cancelled() => {
                    close(&mut socket, 1000).await;
                    return Outcome::Stopped;
                }
                changed = auth_changes.changed() => {
                    if changed.is_err() || auth_changes.borrow().kind != AuthKind::Authenticated {
                        close(&mut socket, 1000).await;
                        return Outcome::Stopped;
                    }
                }
                _ = tick => {
                    if !acked {
                        close(&mut socket, 4000).await;
                        return Outcome::Resume;
                    }
                    acked = false;
                    if self.send(&mut socket, 1, json!(self.seq)).await.is_err() {
                        return Outcome::Resume;
                    }
                }
                Some(command) = self.commands.recv() => match command {
                    Command::Subscribe(guild_id) => {
                        if self.subscribed.insert(guild_id.clone()) {
                            let _ = self.send(&mut socket, 37, subscription(&[guild_id])).await;
                        }
                    }
                    Command::RequestMembers { guild_id, user_ids } => {
                        // Discord caps a by-id request at 100 users.
                        for chunk in user_ids.chunks(100) {
                            let payload = json!({
                                "guild_id": [guild_id],
                                "user_ids": chunk,
                                "presences": false,
                            });
                            let _ = self.send(&mut socket, 8, payload).await;
                        }
                    }
                },
                frame = socket.next() => {
                    let text = match frame {
                        Some(Ok(WsMessage::Text(text))) => text,
                        Some(Ok(WsMessage::Binary(data))) => match inflater.push(&data) {
                            Ok(Some(text)) => text,
                            Ok(None) => continue,
                            Err(_) => return Outcome::Reidentify,
                        },
                        Some(Ok(WsMessage::Close(frame))) => {
                            return close_outcome(frame.map(|frame| u16::from(frame.code)));
                        }
                        Some(Ok(_)) => continue,
                        Some(Err(_)) | None => return Outcome::Resume,
                    };
                    let Ok(envelope) = serde_json::from_str::<Envelope>(&text) else {
                        continue;
                    };
                    match envelope.op {
                        0 => {
                            if let Some(seq) = envelope.s {
                                self.seq = Some(seq);
                            }
                            if let Some(outcome) = self
                                .dispatch(&mut socket, envelope.t, envelope.d, failures)
                                .await
                            {
                                return outcome;
                            }
                        }
                        1 => {
                            if self.send(&mut socket, 1, json!(self.seq)).await.is_err() {
                                return Outcome::Resume;
                            }
                        }
                        7 => {
                            close(&mut socket, 4000).await;
                            return Outcome::Resume;
                        }
                        9 => {
                            let resumable = envelope
                                .d
                                .and_then(|d| serde_json::from_str::<bool>(d.get()).ok())
                                .unwrap_or(false);
                            close(&mut socket, 4000).await;
                            if resumable {
                                return Outcome::Resume;
                            }
                            let wait = self.config.reconnect_base.mul_f64(1.0 + 4.0 * random_fraction());
                            sleep(wait).await;
                            return Outcome::Reidentify;
                        }
                        10 => {
                            let interval_ms = envelope
                                .d
                                .and_then(|d| serde_json::from_str::<Value>(d.get()).ok())
                                .and_then(|d| d.get("heartbeat_interval").and_then(Value::as_u64))
                                .unwrap_or(41_250)
                                .max(1);
                            let period = Duration::from_millis(interval_ms);
                            let mut interval =
                                interval_at(Instant::now() + period.mul_f64(random_fraction()), period);
                            interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
                            heartbeat = Some(interval);
                            if self.hello(&mut socket).await.is_err() {
                                return Outcome::Resume;
                            }
                        }
                        11 => acked = true,
                        _ => {}
                    }
                }
            }
        }
    }

    async fn hello(&mut self, socket: &mut Socket) -> Result<()> {
        if let (Some(session_id), Some(seq)) = (&self.session_id, self.seq) {
            let payload = json!({
                "token": self.token.expose_secret(),
                "session_id": session_id,
                "seq": seq,
            });
            return self.send(socket, 6, payload).await;
        }
        let pacing = self.config.reconnect_base * 5;
        if let Some(last) = self.last_identify {
            let since = last.elapsed();
            if since < pacing {
                sleep(pacing - since).await;
            }
        }
        self.last_identify = Some(Instant::now());
        let payload = json!({
            "token": self.token.expose_secret(),
            "capabilities": CAPABILITIES,
            "properties": identity::super_properties(&self.config.locale),
            "presence": {"status": "unknown", "since": 0, "activities": [], "afk": false},
            "compress": false,
            "client_state": {"guild_versions": {}},
        });
        self.send(socket, 2, payload).await
    }

    async fn send(&self, socket: &mut Socket, op: u8, d: Value) -> Result<()> {
        socket
            .send(WsMessage::Text(json!({"op": op, "d": d}).to_string()))
            .await
            .map_err(|error| EngineError::Network(error.to_string()))
    }

    async fn emit(&self, event: GatewayEvent) {
        let _ = self.events.send(event).await;
    }

    /// Handle a dispatch. Returns an outcome only when the connection must end.
    async fn dispatch(
        &mut self,
        socket: &mut Socket,
        kind: Option<&str>,
        data: Option<&RawValue>,
        failures: &mut u32,
    ) -> Option<Outcome> {
        let (Some(kind), Some(data)) = (kind, data) else {
            return None;
        };
        let parse = || serde_json::from_str::<Value>(data.get()).ok();
        match kind {
            "READY" => {
                let value = parse()?;
                let Ok(ready) = parse_ready(&value) else {
                    return Some(Outcome::Reidentify);
                };
                self.user_id = Some(ready.user.id.clone());
                self.session_id = value
                    .get("session_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                self.resume_url = value
                    .get("resume_gateway_url")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                *failures = 0;
                self.status(ConnectionStatus::Connected, None).await;
                if !self.subscribed.is_empty() {
                    let guilds: Vec<String> = self.subscribed.iter().cloned().collect();
                    let _ = self.send(socket, 37, subscription(&guilds)).await;
                }
                match self.ready.take() {
                    Some(first) => {
                        let _ = first.send(Ok(ready));
                    }
                    None => self.emit(GatewayEvent::Ready(Box::new(ready))).await,
                }
            }
            "RESUMED" => {
                *failures = 0;
                self.status(ConnectionStatus::Connected, None).await;
            }
            "MESSAGE_CREATE" => {
                if let Ok(message) = Message::from_value(&parse()?) {
                    self.emit(GatewayEvent::MessageCreate(Box::new(message)))
                        .await;
                }
            }
            "MESSAGE_UPDATE" => {
                let value = parse()?;
                if value.get("author").is_some()
                    && value.get("content").is_some()
                    && let Ok(message) = Message::from_value(&value)
                {
                    self.emit(GatewayEvent::MessageUpdate(Box::new(message)))
                        .await;
                }
            }
            "MESSAGE_DELETE" => {
                let value = parse()?;
                if let (Some(id), Some(channel_id)) =
                    (text(&value, "id"), text(&value, "channel_id"))
                {
                    self.emit(GatewayEvent::MessageDelete {
                        id,
                        channel_id,
                        guild_id: text(&value, "guild_id"),
                    })
                    .await;
                }
            }
            "MESSAGE_DELETE_BULK" => {
                let value = parse()?;
                let channel_id = text(&value, "channel_id")?;
                let guild_id = text(&value, "guild_id");
                let ids: Vec<String> = value
                    .get("ids")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(|id| id.as_str().map(str::to_owned))
                    .collect();
                for id in ids {
                    self.emit(GatewayEvent::MessageDelete {
                        id,
                        channel_id: channel_id.clone(),
                        guild_id: guild_id.clone(),
                    })
                    .await;
                }
            }
            "TYPING_START" => {
                let value = parse()?;
                self.emit(GatewayEvent::TypingStart {
                    channel_id: text(&value, "channel_id")?,
                    guild_id: text(&value, "guild_id"),
                    user_id: text(&value, "user_id")?,
                    timestamp: value.get("timestamp").and_then(Value::as_i64).unwrap_or(0),
                })
                .await;
            }
            "MESSAGE_ACK" => {
                let value = parse()?;
                self.emit(GatewayEvent::MessageAck {
                    channel_id: text(&value, "channel_id")?,
                    message_id: text(&value, "message_id")?,
                    mention_count: value
                        .get("mention_count")
                        .and_then(Value::as_u64)
                        .map(|count| count as u32),
                })
                .await;
            }
            "CHANNEL_CREATE" | "CHANNEL_UPDATE" => {
                if let Ok(channel) = Channel::from_value(&parse()?, None, &[]) {
                    self.emit(GatewayEvent::ChannelUpsert(Box::new(channel)))
                        .await;
                }
            }
            "CHANNEL_DELETE" => {
                let value = parse()?;
                self.emit(GatewayEvent::ChannelDelete {
                    id: text(&value, "id")?,
                    guild_id: text(&value, "guild_id"),
                })
                .await;
            }
            "GUILD_CREATE" | "GUILD_UPDATE" => {
                let me = self.auth.snapshot().user.map(|user| user.id);
                if let Ok(guild) = Guild::parse(&parse()?, me.as_deref()) {
                    self.emit(GatewayEvent::GuildUpsert(Box::new(guild))).await;
                }
            }
            "GUILD_DELETE" => {
                let value = parse()?;
                self.emit(GatewayEvent::GuildDelete {
                    id: text(&value, "id")?,
                    unavailable: value
                        .get("unavailable")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                })
                .await;
            }
            "THREAD_CREATE" | "THREAD_UPDATE" => {
                if let Ok(thread) = Channel::from_value(&parse()?, None, &[]) {
                    self.emit(GatewayEvent::ThreadUpsert(Box::new(thread)))
                        .await;
                }
            }
            "THREAD_DELETE" => {
                let value = parse()?;
                self.emit(GatewayEvent::ThreadDelete {
                    id: text(&value, "id")?,
                    guild_id: text(&value, "guild_id"),
                    parent_id: text(&value, "parent_id"),
                })
                .await;
            }
            "THREAD_LIST_SYNC" => {
                let value = parse()?;
                let guild_id = text(&value, "guild_id")?;
                let channel_ids = value
                    .get("channel_ids")
                    .and_then(Value::as_array)
                    .map(|ids| {
                        ids.iter()
                            .filter_map(|id| id.as_str().map(str::to_owned))
                            .collect()
                    });
                // `members` lists the signed-in user's thread memberships separately.
                let joined: HashSet<&str> = value
                    .get("members")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(|member| member.get("id").and_then(Value::as_str))
                    .collect();
                let threads = value
                    .get("threads")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(|thread| Channel::from_value(thread, Some(&guild_id), &[]).ok())
                    .map(|mut thread| {
                        thread.joined |= joined.contains(thread.id.as_str());
                        thread
                    })
                    .collect();
                self.emit(GatewayEvent::ThreadListSync {
                    guild_id,
                    channel_ids,
                    threads,
                })
                .await;
            }
            "THREAD_MEMBER_UPDATE" => {
                // Always about the signed-in user (e.g. after joining or unarchiving).
                let value = parse()?;
                self.emit(GatewayEvent::ThreadMembership {
                    thread_id: text(&value, "id")?,
                    guild_id: text(&value, "guild_id"),
                    joined: true,
                })
                .await;
            }
            "THREAD_MEMBERS_UPDATE" => {
                let value = parse()?;
                let me = self.user_id.as_deref()?;
                let added = value
                    .get("added_members")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .any(|member| member.get("user_id").and_then(Value::as_str) == Some(me));
                let removed = value
                    .get("removed_member_ids")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .any(|id| id.as_str() == Some(me));
                if added || removed {
                    self.emit(GatewayEvent::ThreadMembership {
                        thread_id: text(&value, "id")?,
                        guild_id: text(&value, "guild_id"),
                        joined: added,
                    })
                    .await;
                }
            }
            "GUILD_MEMBER_ADD" | "GUILD_MEMBER_UPDATE" => {
                let value = parse()?;
                if let (Some(guild_id), Some(member)) =
                    (text(&value, "guild_id"), Member::from_value(&value))
                {
                    self.emit(GatewayEvent::Members {
                        guild_id,
                        members: vec![member],
                    })
                    .await;
                }
            }
            "GUILD_MEMBERS_CHUNK" => {
                let value = parse()?;
                let guild_id = text(&value, "guild_id")?;
                let members = value
                    .get("members")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Member::from_value)
                    .collect();
                self.emit(GatewayEvent::Members { guild_id, members }).await;
            }
            "GUILD_ROLE_CREATE" | "GUILD_ROLE_UPDATE" => {
                let value = parse()?;
                let guild_id = text(&value, "guild_id")?;
                if let Some(Ok(role)) = value.get("role").map(Role::from_value) {
                    self.emit(GatewayEvent::RoleUpsert { guild_id, role }).await;
                }
            }
            "GUILD_ROLE_DELETE" => {
                let value = parse()?;
                self.emit(GatewayEvent::RoleDelete {
                    guild_id: text(&value, "guild_id")?,
                    role_id: text(&value, "role_id")?,
                })
                .await;
            }
            _ => {}
        }
        None
    }
}

#[derive(Deserialize)]
struct Envelope<'a> {
    op: u8,
    #[serde(borrow)]
    d: Option<&'a RawValue>,
    s: Option<u64>,
    t: Option<&'a str>,
}

fn close_outcome(code: Option<u16>) -> Outcome {
    match code {
        Some(4004) => Outcome::AuthFailed,
        Some(4007 | 4009) => Outcome::Reidentify,
        Some(4010..=4015) => Outcome::Fatal,
        _ => Outcome::Resume,
    }
}

async fn close(socket: &mut Socket, code: u16) {
    let frame = CloseFrame {
        code: CloseCode::from(code),
        reason: "".into(),
    };
    let _ = socket.close(Some(frame)).await;
}

fn subscription(guilds: &[String]) -> Value {
    let entries: serde_json::Map<String, Value> = guilds
        .iter()
        .map(|id| {
            (
                id.clone(),
                json!({"typing": true, "threads": true, "activities": false}),
            )
        })
        .collect();
    json!({"subscriptions": entries})
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}

/// Append the original query string (`v`, `encoding`, `compress`) to a resume URL.
fn with_query(resume: &str, original: &str) -> String {
    let query = original
        .split_once('?')
        .map(|(_, query)| query)
        .unwrap_or("");
    let base = resume.trim_end_matches('/');
    if query.is_empty() {
        format!("{base}/")
    } else {
        format!("{base}/?{query}")
    }
}

fn random_fraction() -> f64 {
    let mut bytes = [0u8; 8];
    if getrandom::fill(&mut bytes).is_err() {
        return 0.5;
    }
    (u64::from_le_bytes(bytes) % 1_000_000) as f64 / 1_000_000.0
}

/// Parse READY, accepting both deduplicated (capability) and legacy shapes.
pub fn parse_ready(value: &Value) -> Result<Ready> {
    let user = User::from_value(
        value
            .get("user")
            .ok_or_else(|| EngineError::Protocol("READY without user".into()))?,
    )?;
    let users: Vec<User> = value
        .get("users")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|user| User::from_value(user).ok())
        .collect();
    let merged: Vec<&Value> = value
        .get("merged_members")
        .and_then(Value::as_array)
        .map(|lists| lists.iter().collect())
        .unwrap_or_default();
    let guilds = value
        .get("guilds")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
        .filter_map(|(index, guild)| {
            let mut guild = Guild::parse(guild, Some(&user.id)).ok()?;
            if guild.me.is_none() {
                // merged_members is aligned with guilds; each entry lists this user's member.
                guild.me = merged
                    .get(index)
                    .and_then(|members| members.as_array())
                    .into_iter()
                    .flatten()
                    .filter_map(Member::from_value)
                    .find(|member| member.user_id == user.id);
            }
            Some(guild)
        })
        .collect();
    let private_channels = value
        .get("private_channels")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|channel| Channel::from_value(channel, None, &users).ok())
        .collect();
    let entries = match value.get("read_state") {
        Some(Value::Object(object)) => object.get("entries").and_then(Value::as_array),
        Some(Value::Array(array)) => Some(array),
        _ => None,
    };
    let read_states = entries
        .into_iter()
        .flatten()
        .filter(|entry| {
            entry
                .get("read_state_type")
                .and_then(Value::as_u64)
                .unwrap_or(0)
                == 0
        })
        .filter_map(|entry| {
            Some(ReadState {
                channel_id: text(entry, "id")?,
                last_message_id: text(entry, "last_message_id"),
                mention_count: entry
                    .get("mention_count")
                    .and_then(Value::as_u64)
                    .unwrap_or(0) as u32,
            })
        })
        .collect();
    Ok(Ready {
        user,
        guilds,
        private_channels,
        read_states,
        users,
    })
}

/// Shared zlib context for a `compress=zlib-stream` connection.
struct Inflater {
    zlib: Decompress,
    pending: Vec<u8>,
    output: Vec<u8>,
}

impl Inflater {
    fn new() -> Self {
        Self {
            zlib: Decompress::new(true),
            pending: Vec::new(),
            output: Vec::with_capacity(64 * 1024),
        }
    }

    /// Buffer `data`; once a full message (ending in the zlib flush suffix) is available,
    /// return its decompressed text.
    fn push(&mut self, data: &[u8]) -> Result<Option<String>> {
        self.pending.extend_from_slice(data);
        if !self.pending.ends_with(&ZLIB_SUFFIX) {
            return Ok(None);
        }
        self.output.clear();
        let mut offset = 0;
        loop {
            if self.output.capacity() - self.output.len() < 16 * 1024 {
                self.output.reserve(64 * 1024);
            }
            let before = self.zlib.total_in();
            let status = self
                .zlib
                .decompress_vec(
                    &self.pending[offset..],
                    &mut self.output,
                    FlushDecompress::Sync,
                )
                .map_err(|error| EngineError::Protocol(error.to_string()))?;
            offset += (self.zlib.total_in() - before) as usize;
            let output_full = self.output.len() == self.output.capacity();
            if (offset >= self.pending.len() && !output_full) || status == Status::StreamEnd {
                break;
            }
        }
        self.pending.clear();
        String::from_utf8(std::mem::take(&mut self.output))
            .map(Some)
            .map_err(|error| EngineError::Protocol(error.to_string()))
    }
}
