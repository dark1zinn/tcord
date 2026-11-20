use keyring::Entry;

const SERVICE: &str = "tcord";
const ACCOUNT: &str = "default";

fn entry() -> Result<Entry, ()> {
    Entry::new(SERVICE, ACCOUNT).map_err(|_| ())
}

pub(crate) fn persist_token(token: &str) -> Result<(), ()> {
    entry()?.set_password(token).map_err(|_| ())
}

pub(crate) fn load_token() -> Result<Option<String>, ()> {
    match entry()?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err(()),
    }
}

pub(crate) fn delete_token() -> Result<(), ()> {
    match entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err(()),
    }
}
