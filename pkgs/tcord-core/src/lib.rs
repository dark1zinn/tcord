//! PyO3 bindings exposing `tcord-engine` to Python as `tcord_core._native`.
//!
//! Every network operation returns an asyncio awaitable driven by one shared Tokio runtime.

use std::sync::Arc;

use jiff::Timestamp;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
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
    bot: bool,
}

impl From<tcord_engine::User> for User {
    fn from(user: tcord_engine::User) -> Self {
        Self {
            id: user.id,
            username: user.username,
            global_name: user.global_name,
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
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Guild {
    id: String,
    name: String,
    icon: Option<String>,
    channels: Vec<Channel>,
    roles: Vec<Role>,
    unavailable: bool,
}

impl From<tcord_engine::Guild> for Guild {
    fn from(guild: tcord_engine::Guild) -> Self {
        Self {
            id: guild.id,
            name: guild.name,
            icon: guild.icon,
            channels: guild.channels.into_iter().map(Channel::from).collect(),
            roles: guild
                .roles
                .into_iter()
                .map(|role| Role {
                    id: role.id,
                    name: role.name,
                    color: role.color,
                })
                .collect(),
            unavailable: guild.unavailable,
        }
    }
}

#[pyclass(frozen, get_all, module = "tcord_core._native")]
#[derive(Clone)]
struct Attachment {
    filename: String,
    url: String,
    size: u64,
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
    author_nick: Option<String>,
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
    embed_titles: Vec<String>,
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
            author_nick: message.author_nick,
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
                    size: attachment.size,
                })
                .collect(),
            embed_titles: message.embed_titles,
            nonce: message.nonce,
            referenced: message
                .referenced
                .map(|referenced| Box::new(Message::from(*referenced))),
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
    #[pyo3(signature = (api_base=None, remote_auth_url=None, credential_store="system", locale="en-US"))]
    fn new(
        api_base: Option<String>,
        remote_auth_url: Option<String>,
        credential_store: &str,
        locale: &str,
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
        let inner = tcord_engine::Session::new(SessionConfig {
            api_base,
            remote_auth_url,
            memory_store,
            locale: Some(locale.to_owned()),
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
    module.add_class::<Channel>()?;
    module.add_class::<Guild>()?;
    module.add_class::<Attachment>()?;
    module.add_class::<MessageReference>()?;
    module.add_class::<Message>()?;
    Ok(())
}
