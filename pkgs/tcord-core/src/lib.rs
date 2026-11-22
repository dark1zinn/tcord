//! PyO3 bindings exposing `tcord-engine` to Python as `tcord_core._native`.
//!
//! Every network operation returns an asyncio awaitable driven by one shared Tokio runtime.

use std::sync::Arc;

use jiff::Timestamp;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3_async_runtimes::tokio::future_into_py;
use tcord_engine::{AuthSnapshot, EngineError, MfaMethod, SessionConfig};

/// Wrapper converting engine errors into the matching `tcord_core._errors` exception.
struct EngineErr(EngineError);

impl From<EngineErr> for PyErr {
    fn from(error: EngineErr) -> Self {
        Python::attach(|py| build_error(py, error.0))
    }
}

fn build_error(py: Python<'_>, error: EngineError) -> PyErr {
    let message = error.to_string();
    let attempt = || -> PyResult<PyErr> {
        let module = py.import("tcord_core._errors")?;
        let class = |name: &str| module.getattr(name);
        let value = match error {
            EngineError::Network(_) => class("NetworkError")?.call1((message,))?,
            EngineError::Unauthorized => class("AuthenticationError")?.call1((message,))?,
            EngineError::Forbidden { code } => {
                class("PermissionDeniedError")?.call1((message, code))?
            }
            EngineError::NotFound => class("NotFoundError")?.call1((message,))?,
            EngineError::RateLimited {
                retry_after,
                global,
            } => class("RateLimitedError")?.call1((message, retry_after, global))?,
            EngineError::CaptchaRequired { service, sitekey } => {
                class("CaptchaRequiredError")?.call1((message, service, sitekey))?
            }
            EngineError::InvalidInput(_) => class("InvalidInputError")?.call1((message,))?,
            EngineError::Http { status, code, .. } => {
                class("HttpError")?.call1((message, status, code))?
            }
            EngineError::Protocol(_) => class("ProtocolError")?.call1((message,))?,
            EngineError::CredentialStore(_) => class("CredentialStoreError")?.call1((message,))?,
            EngineError::Cancelled => class("CancelledError")?.call1((message,))?,
        };
        Ok(PyErr::from_value(value))
    };
    attempt().unwrap_or_else(|error| error)
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct User {
    id: String,
    username: String,
    global_name: Option<String>,
    avatar: Option<String>,
    bot: bool,
}

impl From<tcord_engine::User> for User {
    fn from(user: tcord_engine::User) -> Self {
        Self {
            id: user.id,
            username: user.username,
            global_name: user.global_name,
            avatar: user.avatar,
            bot: user.bot,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct QrUser {
    id: String,
    discriminator: String,
    avatar: Option<String>,
    username: String,
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct AuthState {
    version: u64,
    kind: String,
    mfa_methods: Vec<String>,
    qr_url: Option<String>,
    qr_expires_at: Option<Timestamp>,
    qr_user: Option<QrUser>,
    user: Option<User>,
    failure: Option<String>,
    retry_after: Option<f64>,
    session_persisted: bool,
}

impl From<AuthSnapshot> for AuthState {
    fn from(snapshot: AuthSnapshot) -> Self {
        Self {
            version: snapshot.version,
            kind: snapshot.kind.as_str().into(),
            mfa_methods: snapshot
                .mfa_methods
                .iter()
                .map(|method| method.as_str().into())
                .collect(),
            qr_url: snapshot.qr_url,
            qr_expires_at: snapshot
                .qr_expires_at
                .and_then(|time| Timestamp::try_from(time).ok()),
            qr_user: snapshot.qr_user.map(|user| QrUser {
                id: user.id,
                discriminator: user.discriminator,
                avatar: user.avatar,
                username: user.username,
            }),
            user: snapshot.user.map(User::from),
            failure: snapshot.failure.map(|failure| failure.as_str().into()),
            retry_after: snapshot.retry_after,
            session_persisted: snapshot.session_persisted,
        }
    }
}

#[pymethods]
impl AuthState {
    fn __repr__(&self) -> String {
        format!(
            "AuthState(kind={:?}, version={}, failure={:?})",
            self.kind, self.version, self.failure
        )
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Role {
    id: String,
    name: String,
    color: u32,
    position: i64,
    permissions: u64,
}

impl From<tcord_engine::Role> for Role {
    fn from(role: tcord_engine::Role) -> Self {
        Self {
            id: role.id,
            name: role.name,
            color: role.color,
            position: role.position,
            permissions: role.permissions,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct PermissionOverwrite {
    id: String,
    kind: u8,
    allow: u64,
    deny: u64,
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct ThreadMetadata {
    archived: bool,
    locked: bool,
    archive_timestamp: Option<Timestamp>,
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Channel {
    id: String,
    kind: u8,
    name: Option<String>,
    guild_id: Option<String>,
    position: i64,
    parent_id: Option<String>,
    last_message_id: Option<String>,
    topic: Option<String>,
    recipients: Vec<User>,
    permission_overwrites: Vec<PermissionOverwrite>,
    owner_id: Option<String>,
    thread_metadata: Option<ThreadMetadata>,
    message_count: Option<u32>,
    joined: bool,
}

impl From<tcord_engine::Channel> for Channel {
    fn from(channel: tcord_engine::Channel) -> Self {
        Self {
            id: channel.id,
            kind: channel.kind,
            name: channel.name,
            guild_id: channel.guild_id,
            position: channel.position,
            parent_id: channel.parent_id,
            last_message_id: channel.last_message_id,
            topic: channel.topic,
            recipients: channel.recipients.into_iter().map(User::from).collect(),
            permission_overwrites: channel
                .permission_overwrites
                .into_iter()
                .map(|overwrite| PermissionOverwrite {
                    id: overwrite.id,
                    kind: overwrite.kind,
                    allow: overwrite.allow,
                    deny: overwrite.deny,
                })
                .collect(),
            owner_id: channel.owner_id,
            thread_metadata: channel.thread_metadata.map(|meta| ThreadMetadata {
                archived: meta.archived,
                locked: meta.locked,
                archive_timestamp: meta.archive_timestamp,
            }),
            message_count: channel.message_count,
            joined: channel.joined,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Member {
    user_id: String,
    nick: Option<String>,
    avatar: Option<String>,
    roles: Vec<String>,
}

impl From<tcord_engine::Member> for Member {
    fn from(member: tcord_engine::Member) -> Self {
        Self {
            user_id: member.user_id,
            nick: member.nick,
            avatar: member.avatar,
            roles: member.roles,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Guild {
    id: String,
    name: String,
    icon: Option<String>,
    owner_id: Option<String>,
    channels: Option<Vec<Channel>>,
    threads: Option<Vec<Channel>>,
    roles: Vec<Role>,
    me: Option<Member>,
    unavailable: bool,
}

impl From<tcord_engine::Guild> for Guild {
    fn from(guild: tcord_engine::Guild) -> Self {
        Self {
            id: guild.id,
            name: guild.name,
            icon: guild.icon,
            owner_id: guild.owner_id,
            channels: guild
                .channels
                .map(|channels| channels.into_iter().map(Channel::from).collect()),
            threads: guild
                .threads
                .map(|threads| threads.into_iter().map(Channel::from).collect()),
            roles: guild.roles.into_iter().map(Role::from).collect(),
            me: guild.me.map(Member::from),
            unavailable: guild.unavailable,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Attachment {
    filename: String,
    url: String,
    proxy_url: Option<String>,
    size: u64,
    content_type: Option<String>,
    width: Option<u32>,
    height: Option<u32>,
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct EmbedMedia {
    url: String,
    proxy_url: Option<String>,
    width: Option<u32>,
    height: Option<u32>,
}

impl From<tcord_engine::EmbedMedia> for EmbedMedia {
    fn from(media: tcord_engine::EmbedMedia) -> Self {
        Self {
            url: media.url,
            proxy_url: media.proxy_url,
            width: media.width,
            height: media.height,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Embed {
    kind: String,
    title: Option<String>,
    description: Option<String>,
    url: Option<String>,
    image: Option<EmbedMedia>,
    thumbnail: Option<EmbedMedia>,
    video: Option<EmbedMedia>,
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Sticker {
    id: String,
    name: String,
    format: u8,
}

#[pyclass(frozen, get_all, from_py_object, module = "tcord_core._native")]
#[derive(Clone)]
struct MessageReference {
    message_id: String,
    channel_id: String,
    guild_id: Option<String>,
}

#[pymethods]
impl MessageReference {
    #[new]
    #[pyo3(signature = (message_id, channel_id, guild_id=None))]
    fn new(message_id: String, channel_id: String, guild_id: Option<String>) -> Self {
        Self {
            message_id,
            channel_id,
            guild_id,
        }
    }
}

#[pyclass(frozen, module = "tcord_core._native")]
#[derive(Clone)]
struct Message {
    #[pyo3(get)]
    id: String,
    #[pyo3(get)]
    channel_id: String,
    #[pyo3(get)]
    guild_id: Option<String>,
    #[pyo3(get)]
    author: User,
    #[pyo3(get)]
    member: Option<Member>,
    #[pyo3(get)]
    content: String,
    #[pyo3(get)]
    timestamp: Timestamp,
    #[pyo3(get)]
    edited_timestamp: Option<Timestamp>,
    #[pyo3(get)]
    kind: u8,
    #[pyo3(get)]
    mention_ids: Vec<String>,
    #[pyo3(get)]
    mention_role_ids: Vec<String>,
    #[pyo3(get)]
    mention_everyone: bool,
    #[pyo3(get)]
    attachments: Vec<Attachment>,
    #[pyo3(get)]
    embeds: Vec<Embed>,
    #[pyo3(get)]
    stickers: Vec<Sticker>,
    #[pyo3(get)]
    thread: Option<Channel>,
    #[pyo3(get)]
    nonce: Option<String>,
    referenced: Option<Box<Message>>,
}

#[pymethods]
impl Message {
    #[getter]
    fn referenced(&self) -> Option<Message> {
        self.referenced.as_deref().cloned()
    }
}

impl From<tcord_engine::Message> for Message {
    fn from(message: tcord_engine::Message) -> Self {
        Self {
            id: message.id,
            channel_id: message.channel_id,
            guild_id: message.guild_id,
            author: message.author.into(),
            member: message.member.map(Member::from),
            content: message.content,
            timestamp: message.timestamp,
            edited_timestamp: message.edited_timestamp,
            kind: message.kind,
            mention_ids: message.mention_ids,
            mention_role_ids: message.mention_role_ids,
            mention_everyone: message.mention_everyone,
            attachments: message
                .attachments
                .into_iter()
                .map(|attachment| Attachment {
                    filename: attachment.filename,
                    url: attachment.url,
                    proxy_url: attachment.proxy_url,
                    size: attachment.size,
                    content_type: attachment.content_type,
                    width: attachment.width,
                    height: attachment.height,
                })
                .collect(),
            embeds: message
                .embeds
                .into_iter()
                .map(|embed| Embed {
                    kind: embed.kind,
                    title: embed.title,
                    description: embed.description,
                    url: embed.url,
                    image: embed.image.map(EmbedMedia::from),
                    thumbnail: embed.thumbnail.map(EmbedMedia::from),
                    video: embed.video.map(EmbedMedia::from),
                })
                .collect(),
            stickers: message
                .stickers
                .into_iter()
                .map(|sticker| Sticker {
                    id: sticker.id,
                    name: sticker.name,
                    format: sticker.format,
                })
                .collect(),
            thread: message.thread.map(|thread| Channel::from(*thread)),
            nonce: message.nonce,
            referenced: message
                .referenced
                .map(|referenced| Box::new(Message::from(*referenced))),
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct ReadState {
    channel_id: String,
    last_message_id: Option<String>,
    mention_count: u32,
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Ready {
    user: User,
    guilds: Vec<Guild>,
    private_channels: Vec<Channel>,
    read_states: Vec<ReadState>,
    users: Vec<User>,
}

impl From<tcord_engine::gateway::Ready> for Ready {
    fn from(ready: tcord_engine::gateway::Ready) -> Self {
        Self {
            user: ready.user.into(),
            guilds: ready.guilds.into_iter().map(Guild::from).collect(),
            private_channels: ready
                .private_channels
                .into_iter()
                .map(Channel::from)
                .collect(),
            read_states: ready
                .read_states
                .into_iter()
                .map(|state| ReadState {
                    channel_id: state.channel_id,
                    last_message_id: state.last_message_id,
                    mention_count: state.mention_count,
                })
                .collect(),
            users: ready.users.into_iter().map(User::from).collect(),
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct ThreadPage {
    threads: Vec<Channel>,
    has_more: bool,
}

/// One gateway event. `kind` selects which of the optional fields are set.
#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone, Default)]
struct GatewayEvent {
    kind: String,
    message: Option<Message>,
    message_id: Option<String>,
    channel_id: Option<String>,
    guild_id: Option<String>,
    user_id: Option<String>,
    timestamp: Option<i64>,
    mention_count: Option<u32>,
    channel: Option<Channel>,
    guild: Option<Guild>,
    ready: Option<Ready>,
    status: Option<String>,
    retry_in: Option<f64>,
    unavailable: Option<bool>,
    parent_id: Option<String>,
    channel_ids: Option<Vec<String>>,
    threads: Option<Vec<Channel>>,
    members: Option<Vec<Member>>,
    role: Option<Role>,
    role_id: Option<String>,
    joined: Option<bool>,
}

impl From<tcord_engine::gateway::GatewayEvent> for GatewayEvent {
    fn from(event: tcord_engine::gateway::GatewayEvent) -> Self {
        use tcord_engine::gateway::GatewayEvent as Event;
        let kind = |name: &str| Self {
            kind: name.into(),
            ..Self::default()
        };
        match event {
            Event::Ready(ready) => Self {
                ready: Some((*ready).into()),
                ..kind("ready")
            },
            Event::MessageCreate(message) => Self {
                message: Some((*message).into()),
                ..kind("message_create")
            },
            Event::MessageUpdate(message) => Self {
                message: Some((*message).into()),
                ..kind("message_update")
            },
            Event::MessageDelete {
                id,
                channel_id,
                guild_id,
            } => Self {
                message_id: Some(id),
                channel_id: Some(channel_id),
                guild_id,
                ..kind("message_delete")
            },
            Event::TypingStart {
                channel_id,
                guild_id,
                user_id,
                timestamp,
            } => Self {
                channel_id: Some(channel_id),
                guild_id,
                user_id: Some(user_id),
                timestamp: Some(timestamp),
                ..kind("typing_start")
            },
            Event::MessageAck {
                channel_id,
                message_id,
                mention_count,
            } => Self {
                channel_id: Some(channel_id),
                message_id: Some(message_id),
                mention_count,
                ..kind("message_ack")
            },
            Event::ChannelUpsert(channel) => Self {
                channel: Some((*channel).into()),
                ..kind("channel_upsert")
            },
            Event::ChannelDelete { id, guild_id } => Self {
                channel_id: Some(id),
                guild_id,
                ..kind("channel_delete")
            },
            Event::GuildUpsert(guild) => Self {
                guild: Some((*guild).into()),
                ..kind("guild_upsert")
            },
            Event::GuildDelete { id, unavailable } => Self {
                guild_id: Some(id),
                unavailable: Some(unavailable),
                ..kind("guild_delete")
            },
            Event::Status { status, retry_in } => Self {
                status: Some(status.as_str().into()),
                retry_in,
                ..kind("status")
            },
            Event::SessionInvalidated => kind("session_invalidated"),
            Event::ThreadUpsert(thread) => Self {
                channel: Some((*thread).into()),
                ..kind("thread_upsert")
            },
            Event::ThreadDelete {
                id,
                guild_id,
                parent_id,
            } => Self {
                channel_id: Some(id),
                guild_id,
                parent_id,
                ..kind("thread_delete")
            },
            Event::ThreadMembership {
                thread_id,
                guild_id,
                joined,
            } => Self {
                channel_id: Some(thread_id),
                guild_id,
                joined: Some(joined),
                ..kind("thread_membership")
            },
            Event::ThreadListSync {
                guild_id,
                channel_ids,
                threads,
            } => Self {
                guild_id: Some(guild_id),
                channel_ids,
                threads: Some(threads.into_iter().map(Channel::from).collect()),
                ..kind("thread_list_sync")
            },
            Event::Members { guild_id, members } => Self {
                guild_id: Some(guild_id),
                members: Some(members.into_iter().map(Member::from).collect()),
                ..kind("members")
            },
            Event::RoleUpsert { guild_id, role } => Self {
                guild_id: Some(guild_id),
                role: Some(role.into()),
                ..kind("role_upsert")
            },
            Event::RoleDelete { guild_id, role_id } => Self {
                guild_id: Some(guild_id),
                role_id: Some(role_id),
                ..kind("role_delete")
            },
        }
    }
}

#[pyclass(frozen, module = "tcord_core._native")]
struct Session {
    inner: Arc<tcord_engine::Session>,
}

type Awaitable<'py> = PyResult<Bound<'py, PyAny>>;

#[pymethods]
impl Session {
    #[new]
    #[pyo3(signature = (
        api_base=None,
        remote_auth_url=None,
        credential_store="system",
        locale="en-US",
        gateway_url=None,
        reconnect_base=None,
        cache_dir=None,
    ))]
    fn new(
        api_base: Option<String>,
        remote_auth_url: Option<String>,
        credential_store: &str,
        locale: &str,
        gateway_url: Option<String>,
        reconnect_base: Option<f64>,
        cache_dir: Option<std::path::PathBuf>,
    ) -> PyResult<Self> {
        let memory_store = match credential_store {
            "system" => false,
            "memory" => true,
            other => {
                return Err(PyValueError::new_err(format!(
                    "unknown credential_store {other:?}"
                )));
            }
        };
        let reconnect_base = reconnect_base
            .map(|seconds| {
                std::time::Duration::try_from_secs_f64(seconds)
                    .map_err(|_| PyValueError::new_err("reconnect_base must be positive"))
            })
            .transpose()?;
        let inner = tcord_engine::Session::new(SessionConfig {
            api_base,
            remote_auth_url,
            gateway_url,
            memory_store,
            locale: Some(locale.to_owned()),
            reconnect_base,
            cache_dir,
            media_hosts: None,
        })
        .map_err(EngineErr)?;
        Ok(Self {
            inner: Arc::new(inner),
        })
    }

    fn auth_state(&self) -> AuthState {
        self.inner.auth().snapshot().into()
    }

    fn cancel_qr_login(&self) {
        self.inner.auth().cancel_qr();
    }

    fn close(&self) {
        self.inner.close();
    }

    fn wait_auth_change<'py>(&self, py: Python<'py>, after_version: u64) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(py, async move {
            Ok(AuthState::from(auth.wait_change(after_version).await))
        })
    }

    fn resume_session<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(py, async move { Ok(AuthState::from(auth.resume().await)) })
    }

    fn login_password<'py>(
        &self,
        py: Python<'py>,
        login: String,
        password: String,
    ) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(py, async move {
            let snapshot = auth
                .login_password(&login, &password)
                .await
                .map_err(EngineErr)?;
            Ok(AuthState::from(snapshot))
        })
    }

    fn submit_mfa<'py>(&self, py: Python<'py>, method: String, code: String) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        let method = MfaMethod::parse(&method).ok_or_else(|| {
            EngineErr(EngineError::InvalidInput(format!(
                "unknown MFA method {method:?}"
            )))
        })?;
        future_into_py(py, async move {
            let snapshot = auth.submit_mfa(method, &code).await.map_err(EngineErr)?;
            Ok(AuthState::from(snapshot))
        })
    }

    fn send_mfa_sms<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(py, async move {
            Ok(auth.send_mfa_sms().await.map_err(EngineErr)?)
        })
    }

    fn start_qr_login<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(
            py,
            async move { Ok(AuthState::from(auth.start_qr().await)) },
        )
    }

    fn logout<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(py, async move { Ok(AuthState::from(auth.logout().await)) })
    }

    fn set_remember_session<'py>(&self, py: Python<'py>, enabled: bool) -> Awaitable<'py> {
        let auth = Arc::clone(self.inner.auth());
        future_into_py(py, async move {
            Ok(AuthState::from(auth.set_remember_session(enabled).await))
        })
    }

    fn connect_gateway<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let ready = inner.connect_gateway().await.map_err(EngineErr)?;
            Ok(Ready::from(ready))
        })
    }

    fn next_event<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            Ok(inner.next_event().await.map(GatewayEvent::from))
        })
    }

    fn subscribe_guild(&self, guild_id: String) {
        self.inner.subscribe_guild(guild_id);
    }

    fn disconnect_gateway(&self) {
        self.inner.disconnect_gateway();
    }

    fn fetch_guilds<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let guilds = inner.fetch_guilds().await.map_err(EngineErr)?;
            Ok(guilds.into_iter().map(Guild::from).collect::<Vec<_>>())
        })
    }

    fn fetch_guild_channels<'py>(&self, py: Python<'py>, guild_id: String) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let channels = inner
                .fetch_guild_channels(&guild_id)
                .await
                .map_err(EngineErr)?;
            Ok(channels.into_iter().map(Channel::from).collect::<Vec<_>>())
        })
    }

    fn fetch_private_channels<'py>(&self, py: Python<'py>) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let channels = inner.fetch_private_channels().await.map_err(EngineErr)?;
            Ok(channels.into_iter().map(Channel::from).collect::<Vec<_>>())
        })
    }

    #[pyo3(signature = (channel_id, before=None, limit=50))]
    fn fetch_messages<'py>(
        &self,
        py: Python<'py>,
        channel_id: String,
        before: Option<String>,
        limit: u32,
    ) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let messages = inner
                .fetch_messages(&channel_id, before.as_deref(), limit)
                .await
                .map_err(EngineErr)?;
            Ok(messages.into_iter().map(Message::from).collect::<Vec<_>>())
        })
    }

    #[pyo3(signature = (channel_id, content, reply_to=None, nonce=None))]
    fn send_message<'py>(
        &self,
        py: Python<'py>,
        channel_id: String,
        content: String,
        reply_to: Option<MessageReference>,
        nonce: Option<String>,
    ) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        let reply_to = reply_to.map(|reference| tcord_engine::MessageReference {
            message_id: reference.message_id,
            channel_id: reference.channel_id,
            guild_id: reference.guild_id,
        });
        future_into_py(py, async move {
            let message = inner
                .send_message(&channel_id, &content, reply_to, nonce)
                .await
                .map_err(EngineErr)?;
            Ok(Message::from(message))
        })
    }

    fn edit_message<'py>(
        &self,
        py: Python<'py>,
        channel_id: String,
        message_id: String,
        content: String,
    ) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let message = inner
                .edit_message(&channel_id, &message_id, &content)
                .await
                .map_err(EngineErr)?;
            Ok(Message::from(message))
        })
    }

    fn delete_message<'py>(
        &self,
        py: Python<'py>,
        channel_id: String,
        message_id: String,
    ) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            Ok(inner
                .delete_message(&channel_id, &message_id)
                .await
                .map_err(EngineErr)?)
        })
    }

    fn trigger_typing<'py>(&self, py: Python<'py>, channel_id: String) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            Ok(inner.trigger_typing(&channel_id).await.map_err(EngineErr)?)
        })
    }

    fn ack_message<'py>(
        &self,
        py: Python<'py>,
        channel_id: String,
        message_id: String,
    ) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            Ok(inner
                .ack_message(&channel_id, &message_id)
                .await
                .map_err(EngineErr)?)
        })
    }

    /// Bytes of a Discord CDN or media-proxy URL (cached on disk).
    fn fetch_media<'py>(&self, py: Python<'py>, url: String) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let bytes = inner.fetch_media(&url).await.map_err(EngineErr)?;
            Ok(PyBytesOwned(bytes))
        })
    }

    fn request_members(&self, guild_id: String, user_ids: Vec<String>) {
        self.inner.request_members(guild_id, user_ids);
    }

    #[pyo3(signature = (channel_id, archived=false, offset=0))]
    fn search_threads<'py>(
        &self,
        py: Python<'py>,
        channel_id: String,
        archived: bool,
        offset: u32,
    ) -> Awaitable<'py> {
        let inner = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let page = inner
                .search_threads(&channel_id, archived, offset)
                .await
                .map_err(EngineErr)?;
            Ok(ThreadPage {
                threads: page.threads.into_iter().map(Channel::from).collect(),
                has_more: page.has_more,
            })
        })
    }
}

/// Owned bytes returned to Python as `bytes` without an intermediate list.
struct PyBytesOwned(Vec<u8>);

impl<'py> IntoPyObject<'py> for PyBytesOwned {
    type Target = PyBytes;
    type Output = Bound<'py, PyBytes>;
    type Error = std::convert::Infallible;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        Ok(PyBytes::new(py, &self.0))
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let mut builder = tokio::runtime::Builder::new_multi_thread();
    builder
        .worker_threads(2)
        .thread_name("tcord-engine")
        .enable_all();
    pyo3_async_runtimes::tokio::init(builder);
    module.add_class::<Session>()?;
    module.add_class::<AuthState>()?;
    module.add_class::<QrUser>()?;
    module.add_class::<User>()?;
    module.add_class::<Role>()?;
    module.add_class::<PermissionOverwrite>()?;
    module.add_class::<ThreadMetadata>()?;
    module.add_class::<Channel>()?;
    module.add_class::<Member>()?;
    module.add_class::<Guild>()?;
    module.add_class::<Attachment>()?;
    module.add_class::<EmbedMedia>()?;
    module.add_class::<Embed>()?;
    module.add_class::<Sticker>()?;
    module.add_class::<MessageReference>()?;
    module.add_class::<Message>()?;
    module.add_class::<ReadState>()?;
    module.add_class::<Ready>()?;
    module.add_class::<GatewayEvent>()?;
    module.add_class::<ThreadPage>()?;
    Ok(())
}
