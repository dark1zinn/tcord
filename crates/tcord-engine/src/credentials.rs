use std::sync::Mutex;

use keyring::Entry;
use secrecy::{ExposeSecret, SecretString};

use crate::error::{EngineError, Result};

/// Where an authenticated session token is persisted.
pub trait CredentialStore: Send + Sync {
    fn load(&self) -> Result<Option<SecretString>>;
    fn save(&self, token: &SecretString) -> Result<()>;
    fn delete(&self) -> Result<()>;
}

/// Operating-system credential store (Secret Service / keyutils, Keychain, Credential Manager).
pub struct KeyringStore {
    service: String,
    user: String,
}

impl KeyringStore {
    pub fn new() -> Self {
        Self {
            service: "tcord".into(),
            user: "session".into(),
        }
    }

    fn entry(&self) -> Result<Entry> {
        Entry::new(&self.service, &self.user).map_err(store_error)
    }
}

impl Default for KeyringStore {
    fn default() -> Self {
        Self::new()
    }
}

impl CredentialStore for KeyringStore {
    fn load(&self) -> Result<Option<SecretString>> {
        match self.entry()?.get_password() {
            Ok(token) => Ok(Some(SecretString::from(token))),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(store_error(error)),
        }
    }

    fn save(&self, token: &SecretString) -> Result<()> {
        self.entry()?
            .set_password(token.expose_secret())
            .map_err(store_error)
    }

    fn delete(&self) -> Result<()> {
        match self.entry()?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(store_error(error)),
        }
    }
}

/// Process-local store: the session is forgotten when Tcord exits.
#[derive(Default)]
pub struct MemoryStore {
    token: Mutex<Option<SecretString>>,
}

impl CredentialStore for MemoryStore {
    fn load(&self) -> Result<Option<SecretString>> {
        Ok(self.token.lock().expect("memory store poisoned").clone())
    }

    fn save(&self, token: &SecretString) -> Result<()> {
        *self.token.lock().expect("memory store poisoned") = Some(token.clone());
        Ok(())
    }

    fn delete(&self) -> Result<()> {
        *self.token.lock().expect("memory store poisoned") = None;
        Ok(())
    }
}

fn store_error(error: keyring::Error) -> EngineError {
    EngineError::CredentialStore(error.to_string())
}
