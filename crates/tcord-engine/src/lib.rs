//! Discord engine for Tcord: authentication, REST and gateway work.
//! This crate has no Python dependency; `pkgs/tcord-core` exposes it to Python.

pub mod auth;
pub mod credentials;
pub mod discord;
pub mod error;
pub mod http;
pub mod models;
mod remote_auth;
mod session;

pub use auth::AuthController;
pub use credentials::{CredentialStore, KeyringStore, MemoryStore};
pub use discord::{Attachment, Channel, Guild, Message, MessageReference, Role, Timestamp};
pub use error::{EngineError, Result};
pub use http::DiscordHttp;
pub use models::{AuthFailure, AuthKind, AuthSnapshot, MfaMethod, QrUser, User};
pub use session::{Session, SessionConfig};
