//! PyO3 bindings exposing `tcord-engine` to Python as `tcord_core._native`.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use tcord_engine::{AuthSnapshot, EngineError, MfaMethod, SessionConfig};

#[pyclass(frozen, get_all, module = "tcord_core._native")]
struct AuthState {
    kind: String,
    qr_payload: Option<String>,
    expires_at: Option<String>,
    mfa_kind: Option<String>,
    user_id: Option<String>,
    username: Option<String>,
    message: Option<String>,
    session_will_not_be_saved: bool,
}

impl From<AuthSnapshot> for AuthState {
    fn from(snapshot: AuthSnapshot) -> Self {
        Self {
            kind: snapshot.kind.as_str().into(),
            qr_payload: snapshot.qr_url,
            expires_at: None,
            mfa_kind: snapshot
                .mfa_methods
                .first()
                .map(|method| method.as_str().into()),
            user_id: snapshot.user.as_ref().map(|user| user.id.clone()),
            username: snapshot.user.map(|user| user.username),
            message: snapshot.failure.map(|failure| failure.as_str().into()),
            session_will_not_be_saved: !snapshot.session_persisted,
        }
    }
}

#[pymethods]
impl AuthState {
    fn __repr__(&self) -> String {
        format!("AuthState(kind={:?})", self.kind)
    }
}

#[pyclass(frozen, module = "tcord_core._native")]
struct Session {
    inner: tcord_engine::Session,
}

fn py_error(error: EngineError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

#[pymethods]
impl Session {
    #[new]
    #[pyo3(signature = (api_base=None, remote_auth_url=None, credential_store="system"))]
    fn new(
        api_base: Option<String>,
        remote_auth_url: Option<String>,
        credential_store: &str,
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
        })
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
        Ok(Self { inner })
    }

    fn login_with_password(&self, email: String, password: String) -> PyResult<AuthState> {
        self.inner
            .login_with_password(email, password)
            .map(Into::into)
            .map_err(py_error)
    }

    fn submit_mfa(&self, code: String) -> PyResult<AuthState> {
        let method = self
            .inner
            .auth_state()
            .mfa_methods
            .first()
            .copied()
            .unwrap_or(MfaMethod::Totp);
        self.inner
            .submit_mfa(method, code)
            .map(Into::into)
            .map_err(py_error)
    }

    fn begin_qr_login(&self) -> AuthState {
        self.inner.begin_qr_login().into()
    }

    fn resume_stored_session(&self) -> AuthState {
        self.inner.resume_stored_session().into()
    }

    fn logout(&self, py: Python<'_>) -> AuthState {
        py.detach(|| self.inner.logout()).into()
    }

    fn auth_state(&self) -> AuthState {
        self.inner.auth_state().into()
    }

    fn get_guilds(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| self.inner.get_guilds()).map_err(py_error)
    }

    fn get_channels(&self, py: Python<'_>, guild_id: String) -> PyResult<String> {
        py.detach(|| self.inner.get_channels(&guild_id))
            .map_err(py_error)
    }

    fn get_dms(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| self.inner.get_dms()).map_err(py_error)
    }

    #[pyo3(signature = (channel_id, before=None, limit=50))]
    fn load_message_history(
        &self,
        py: Python<'_>,
        channel_id: String,
        before: Option<String>,
        limit: u16,
    ) -> PyResult<String> {
        py.detach(|| self.inner.load_message_history(&channel_id, before, limit))
            .map_err(py_error)
    }

    fn send_message(
        &self,
        py: Python<'_>,
        channel_id: String,
        content: String,
    ) -> PyResult<String> {
        py.detach(|| self.inner.send_message(&channel_id, &content))
            .map_err(py_error)
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Session>()?;
    module.add_class::<AuthState>()?;
    Ok(())
}
