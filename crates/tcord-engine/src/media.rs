//! Download of Discord-hosted media (avatars, icons, emojis, stickers, attachments) with a
//! size-capped on-disk cache.
//!
//! Only Discord's CDN and media proxy hosts are fetched, so message content can never make
//! the client contact arbitrary servers (which would leak the user's IP address).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use futures_util::StreamExt;
use reqwest::{Client, Url, redirect};
use rsa::sha2::{Digest, Sha256};

use crate::error::{EngineError, Result};
use crate::identity;

/// Largest single download accepted.
pub const MAX_MEDIA_BYTES: usize = 16 * 1024 * 1024;
/// Total cache size before the least recently used files are evicted.
const CACHE_LIMIT_BYTES: u64 = 256 * 1024 * 1024;
/// Redirects followed for one download (each target is host-checked).
const MAX_REDIRECTS: usize = 5;
/// Signed attachment parameters; they change on every refresh, so they are not part of the key.
const VOLATILE_PARAMS: [&str; 3] = ["ex", "is", "hm"];

/// Which hosts may be fetched.
#[derive(Clone, Debug)]
pub enum HostPolicy {
    /// `cdn.discordapp.com`, `media.discordapp.net` and `*.discordapp.net` over HTTPS.
    Discord,
    /// Any host. Only compiled for the engine's own tests (`test-hosts` feature).
    #[cfg(feature = "test-hosts")]
    Any,
    /// Only these `host:port` origins (`test-hosts` feature).
    #[cfg(feature = "test-hosts")]
    Origins(Vec<String>),
}

impl HostPolicy {
    fn allows(&self, url: &Url) -> bool {
        match self {
            #[cfg(feature = "test-hosts")]
            Self::Any => true,
            #[cfg(feature = "test-hosts")]
            Self::Origins(origins) => url
                .host_str()
                .zip(url.port_or_known_default())
                .is_some_and(|(host, port)| origins.contains(&format!("{host}:{port}"))),
            Self::Discord => {
                url.scheme() == "https"
                    && url.host_str().is_some_and(|host| {
                        host == "cdn.discordapp.com"
                            || host == "media.discordapp.net"
                            || host.ends_with(".discordapp.net")
                    })
            }
        }
    }
}

pub struct MediaCache {
    client: Client,
    dir: Option<PathBuf>,
    policy: HostPolicy,
    /// Numbers temporary files.
    writes: AtomicU32,
    /// Bytes in the cache directory (`None` until first measured).
    usage: Arc<Mutex<Option<u64>>>,
    limit: u64,
}

impl MediaCache {
    pub fn new(dir: Option<PathBuf>, policy: HostPolicy) -> Result<Self> {
        // Every hop must pass the same host check as the original URL; a refused redirect is
        // returned as-is (a 3xx, mapped to an error) without contacting its target.
        let hops = policy.clone();
        let redirects = redirect::Policy::custom(move |attempt| {
            if attempt.previous().len() < MAX_REDIRECTS && hops.allows(attempt.url()) {
                attempt.follow()
            } else {
                attempt.stop()
            }
        });
        let client = Client::builder()
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(10))
            .user_agent(identity::user_agent())
            .redirect(redirects)
            .build()
            .map_err(|error| EngineError::Network(error.to_string()))?;
        Ok(Self {
            client,
            dir,
            policy,
            writes: AtomicU32::new(0),
            usage: Arc::new(Mutex::new(None)),
            limit: CACHE_LIMIT_BYTES,
        })
    }

    /// Bytes of `url`, from the cache when present.
    pub async fn fetch(&self, url: &str) -> Result<Vec<u8>> {
        let parsed =
            Url::parse(url).map_err(|error| EngineError::InvalidInput(error.to_string()))?;
        if !self.policy.allows(&parsed) {
            return Err(EngineError::InvalidInput(format!(
                "media host not allowed: {}",
                parsed.host_str().unwrap_or("")
            )));
        }
        let path = self.dir.as_ref().map(|dir| dir.join(cache_key(&parsed)));
        if let Some(path) = &path
            && let Some(bytes) = read_cached(path).await
        {
            return Ok(bytes);
        }
        let bytes = self.download(parsed).await?;
        if let Some(path) = path {
            self.store(path, bytes.clone()).await;
        }
        Ok(bytes)
    }

    async fn download(&self, url: Url) -> Result<Vec<u8>> {
        let response = self
            .client
            .get(url)
            .send()
            .await
            .map_err(|error| EngineError::Network(error.to_string()))?;
        let status = response.status().as_u16();
        match status {
            200..=299 => {}
            404 => return Err(EngineError::NotFound),
            403 => return Err(EngineError::Forbidden { code: None }),
            _ => {
                return Err(EngineError::Http {
                    status,
                    code: None,
                    message: "media download failed".into(),
                });
            }
        }
        if response
            .content_length()
            .is_some_and(|length| length > MAX_MEDIA_BYTES as u64)
        {
            return Err(EngineError::InvalidInput("media too large".into()));
        }
        let mut bytes = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|error| EngineError::Network(error.to_string()))?;
            if bytes.len() + chunk.len() > MAX_MEDIA_BYTES {
                return Err(EngineError::InvalidInput("media too large".into()));
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes)
    }

    async fn store(&self, path: PathBuf, bytes: Vec<u8>) {
        let write = self.writes.fetch_add(1, Ordering::Relaxed);
        let usage = Arc::clone(&self.usage);
        let limit = self.limit;
        // Cache failures only cost a re-download, so they are ignored.
        let _ = tokio::task::spawn_blocking(move || {
            let Some(dir) = path.parent() else {
                return;
            };
            if std::fs::create_dir_all(dir).is_err() {
                return;
            }
            // Concurrent fetches of one URL each write their own file; the rename is atomic.
            let temporary = path.with_extension(format!("{}-{write}.part", std::process::id()));
            if std::fs::write(&temporary, &bytes).is_err() {
                return;
            }
            let replaced = std::fs::metadata(&path).map_or(0, |metadata| metadata.len());
            if std::fs::rename(&temporary, &path).is_err() {
                let _ = std::fs::remove_file(&temporary);
                return;
            }
            enforce_budget(&usage, dir, bytes.len() as u64, replaced, limit);
        })
        .await;
    }
}

/// Stable file name for a URL, ignoring the volatile signature parameters.
pub fn cache_key(url: &Url) -> String {
    let mut stable = url.clone();
    let kept: Vec<(String, String)> = url
        .query_pairs()
        .filter(|(key, _)| !VOLATILE_PARAMS.contains(&key.as_ref()))
        .map(|(key, value)| (key.into_owned(), value.into_owned()))
        .collect();
    stable.set_query(None);
    if !kept.is_empty() {
        stable.query_pairs_mut().extend_pairs(kept);
    }
    let digest = Sha256::digest(stable.as_str().as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

async fn read_cached(path: &Path) -> Option<Vec<u8>> {
    let path = path.to_owned();
    tokio::task::spawn_blocking(move || {
        let bytes = std::fs::read(&path).ok()?;
        // Refresh the modification time so eviction approximates least-recently-used.
        if let Ok(file) = std::fs::File::options().append(true).open(&path) {
            let _ = file.set_modified(SystemTime::now());
        }
        Some(bytes)
    })
    .await
    .ok()
    .flatten()
}

/// Account for a cache file of `added` bytes that replaced `removed` bytes in `dir`, evicting
/// the oldest files once the cache is over `limit`.
///
/// The directory is scanned once to learn its size and again only when the budget is
/// exceeded, so every write is checked without listing the cache each time.
fn enforce_budget(usage: &Mutex<Option<u64>>, dir: &Path, added: u64, removed: u64, limit: u64) {
    let Ok(mut usage) = usage.lock() else {
        return;
    };
    let total = match *usage {
        Some(total) => total.saturating_sub(removed).saturating_add(added),
        None => cache_files(dir).iter().map(|(_, size, _)| size).sum(),
    };
    *usage = Some(if total > limit {
        evict_oldest(dir, limit)
    } else {
        total
    });
}

fn cache_files(dir: &Path) -> Vec<(SystemTime, u64, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    entries
        .filter_map(|entry| {
            let entry = entry.ok()?;
            let metadata = entry.metadata().ok()?;
            if !metadata.is_file() {
                return None;
            }
            Some((metadata.modified().ok()?, metadata.len(), entry.path()))
        })
        .collect()
}

/// Remove the least recently used files until at most `limit` bytes remain (files that
/// cannot be removed are skipped). Returns the bytes left.
fn evict_oldest(dir: &Path, limit: u64) -> u64 {
    let mut files = cache_files(dir);
    let mut total: u64 = files.iter().map(|(_, size, _)| size).sum();
    files.sort_by_key(|(modified, _, _)| *modified);
    for (_, size, path) in files {
        if total <= limit {
            break;
        }
        if std::fs::remove_file(&path).is_ok() {
            total -= size;
        }
    }
    total
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signature_params_do_not_change_the_key() {
        let first =
            Url::parse("https://cdn.discordapp.com/a/b.png?ex=1&is=2&hm=3&size=64").unwrap();
        let second = Url::parse("https://cdn.discordapp.com/a/b.png?size=64&ex=9&hm=8").unwrap();
        let other = Url::parse("https://cdn.discordapp.com/a/b.png?size=128").unwrap();
        assert_eq!(cache_key(&first), cache_key(&second));
        assert_ne!(cache_key(&first), cache_key(&other));
    }

    #[test]
    fn only_discord_https_hosts_are_allowed() {
        let policy = HostPolicy::Discord;
        for allowed in [
            "https://cdn.discordapp.com/emojis/1.png",
            "https://media.discordapp.net/stickers/1.gif",
            "https://images-ext-1.discordapp.net/external/x",
        ] {
            assert!(policy.allows(&Url::parse(allowed).unwrap()), "{allowed}");
        }
        for denied in [
            "http://cdn.discordapp.com/emojis/1.png",
            "https://example.com/a.png",
            "https://cdn.discordapp.com.evil.net/a.png",
            "https://discordapp.net.example/a.png",
        ] {
            assert!(!policy.allows(&Url::parse(denied).unwrap()), "{denied}");
        }
    }

    fn temp_cache(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tcord-budget-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(dir: &Path, name: &str, size: usize, age_secs: u64) {
        let path = dir.join(name);
        std::fs::write(&path, vec![0u8; size]).unwrap();
        let file = std::fs::File::options().append(true).open(&path).unwrap();
        file.set_modified(SystemTime::now() - Duration::from_secs(age_secs))
            .unwrap();
    }

    #[test]
    fn every_write_stays_within_the_budget() {
        let dir = temp_cache("each");
        let usage = Mutex::new(None);
        write(&dir, "old", 600, 30);
        enforce_budget(&usage, &dir, 600, 0, 1000);
        assert_eq!(*usage.lock().unwrap(), Some(600)); // first write measures, no eviction
        write(&dir, "new", 600, 0);
        enforce_budget(&usage, &dir, 600, 0, 1000); // the second write already overflows
        assert!(!dir.join("old").exists());
        assert!(dir.join("new").exists());
        assert_eq!(*usage.lock().unwrap(), Some(600));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn rewriting_a_file_counts_only_the_difference() {
        let dir = temp_cache("rewrite");
        let usage = Mutex::new(Some(900));
        write(&dir, "a", 900, 0);
        enforce_budget(&usage, &dir, 900, 900, 1000);
        assert_eq!(*usage.lock().unwrap(), Some(900));
        assert!(dir.join("a").exists());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn unremovable_files_do_not_fail_eviction() {
        let dir = temp_cache("missing");
        let usage = Mutex::new(Some(5000)); // stale count: the files are already gone
        enforce_budget(&usage, &dir, 10, 0, 1000);
        assert_eq!(*usage.lock().unwrap(), Some(0));
        let _ = std::fs::remove_dir_all(dir);
    }
}
