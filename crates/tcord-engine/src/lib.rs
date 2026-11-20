//! Discord engine for Tcord: authentication, REST and (later) gateway work.
//! This crate has no Python dependency; `pkgs/tcord-core` exposes it to Python.

mod credentials;
pub mod models;
mod remote_auth;
mod session;

pub use models::{AuthStatus, AuthView};
pub use session::Session;
