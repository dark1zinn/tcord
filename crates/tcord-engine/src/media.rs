//! Download of Discord-hosted media (avatars, icons, emojis, stickers, attachments) with a
//! size-capped on-disk cache.
//!
//! Only Discord's CDN and media proxy hosts are fetched, so message content can never make
//! the client contact arbitrary servers (which would leak the user's IP address).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, SystemTime};

use futures_util::StreamExt;
use reqwest::{Client, Url};
use rsa::sha2::{Digest, Sha256};

use crate::error::{EngineError, Result};
use crate::identity;

/// Largest single download accepted.
pub const MAX_MEDIA_BYTES: usize = 16 * 1024 * 1024;
/// Total cache size before the least recently used files are evicted.
const CACHE_LIMIT_BYTES: u64 = 256 * 1024 * 1024;
/// Evict after this many writes (scanning the directory is not free).
const EVICT_EVERY: u32 = 32;
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
}

impl HostPolicy {
    fn allows(&self, url: &Url) -> bool {
        match self {
            #[cfg(feature = "test-hosts")]
            Self::Any => true,
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
    writes: AtomicU32,
}

impl MediaCache {
    pub fn new(dir: Option<PathBuf>, policy: HostPolicy) -> Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(10))
            .user_agent(identity::user_agent())
            .build()
            .map_err(|error| EngineError::Network(error.to_string()))?;
        Ok(Self {
            client,
            dir,
            policy,
            writes: AtomicU32::new(0),
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
        let evict = self.writes.fetch_add(1, Ordering::Relaxed) % EVICT_EVERY == EVICT_EVERY - 1;
        // Cache failures only cost a re-download, so they are ignored.
        let _ = tokio::task::spawn_blocking(move || {
            let Some(dir) = path.parent() else {
                return;
            };
            if std::fs::create_dir_all(dir).is_err() {
                return;
            }
            let temporary = path.with_extension("part");
            if std::fs::write(&temporary, &bytes).is_ok() {
                let _ = std::fs::rename(&temporary, &path);
            }
            if evict {
                evict_oldest(dir, CACHE_LIMIT_BYTES);
            }
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

fn evict_oldest(dir: &Path, limit: u64) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut files: Vec<(SystemTime, u64, PathBuf)> = entries
        .filter_map(|entry| {
            let entry = entry.ok()?;
            let metadata = entry.metadata().ok()?;
            if !metadata.is_file() {
                return None;
            }
            Some((metadata.modified().ok()?, metadata.len(), entry.path()))
        })
        .collect();
    let mut total: u64 = files.iter().map(|(_, size, _)| size).sum();
    if total <= limit {
        return;
    }
    files.sort_by_key(|(modified, _, _)| *modified);
    for (_, size, path) in files {
        if total <= limit {
            break;
        }
        if std::fs::remove_file(&path).is_ok() {
            total -= size;
        }
    }
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
}
