//! PyO3 bindings exposing `tcord-engine` to Python as `tcord_core._native`.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use tcord_engine::AuthView;

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

impl From<AuthView> for AuthState {
    fn from(view: AuthView) -> Self {
        Self {
            kind: view.kind.into(),
            qr_payload: view.qr_payload,
            expires_at: view.expires_at,
            mfa_kind: view.mfa_kind,
            user_id: view.user_id,
            username: view.username,
            message: view.message,
            session_will_not_be_saved: view.session_will_not_be_saved,
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

fn auth(result: Result<AuthView, String>) -> PyResult<AuthState> {
    result.map(AuthState::from).map_err(PyValueError::new_err)
}

#[pymethods]
impl Session {
    #[new]
    #[pyo3(signature = (api_base=None))]
    fn new(api_base: Option<String>) -> PyResult<Self> {
        let inner = tcord_engine::Session::new(api_base)
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
        Ok(Self { inner })
    }

    fn begin_credentials(&self) -> AuthState {
        self.inner.begin_credentials().into()
    }

    fn login_with_password(&self, email: String, password: String) -> PyResult<AuthState> {
        auth(self.inner.login_with_password(email, password))
    }

    fn submit_mfa(&self, code: String) -> PyResult<AuthState> {
        auth(self.inner.submit_mfa(code))
    }

    fn begin_qr_login(&self) -> PyResult<AuthState> {
        auth(self.inner.begin_qr_login())
    }

    fn resume_stored_session(&self) -> PyResult<AuthState> {
        auth(self.inner.resume_stored_session())
    }

    fn logout(&self) -> PyResult<AuthState> {
        auth(self.inner.logout())
    }

    fn auth_state(&self) -> AuthState {
        self.inner.auth_state().into()
    }

    fn get_guilds(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| self.inner.get_guilds())
            .map_err(PyValueError::new_err)
    }

    fn get_channels(&self, py: Python<'_>, guild_id: String) -> PyResult<String> {
        py.detach(|| self.inner.get_channels(guild_id))
            .map_err(PyValueError::new_err)
    }

    fn get_dms(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| self.inner.get_dms())
            .map_err(PyValueError::new_err)
    }

    #[pyo3(signature = (channel_id, before=None, limit=50))]
    fn load_message_history(
        &self,
        py: Python<'_>,
        channel_id: String,
        before: Option<String>,
        limit: u16,
    ) -> PyResult<String> {
        py.detach(|| self.inner.load_message_history(channel_id, before, limit))
            .map_err(PyValueError::new_err)
    }

    fn send_message(
        &self,
        py: Python<'_>,
        channel_id: String,
        content: String,
    ) -> PyResult<String> {
        py.detach(|| self.inner.send_message(channel_id, content))
            .map_err(PyValueError::new_err)
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Session>()?;
    module.add_class::<AuthState>()?;
    Ok(())
}
