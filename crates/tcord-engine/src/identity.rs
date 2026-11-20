//! The web-client identity Tcord presents to Discord (`User-Agent` and `X-Super-Properties`).
//!
//! The build number is pinned in `client-identity.json` and refreshed by a scheduled workflow.

use std::sync::LazyLock;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
struct PinnedIdentity {
    client_build_number: u64,
    chrome_version: String,
}

static PINNED: LazyLock<PinnedIdentity> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../client-identity.json"))
        .expect("client-identity.json is valid")
});

fn os_name() -> &'static str {
    match std::env::consts::OS {
        "macos" => "Mac OS X",
        "windows" => "Windows",
        _ => "Linux",
    }
}

fn platform_token() -> &'static str {
    match std::env::consts::OS {
        "macos" => "Macintosh; Intel Mac OS X 10_15_7",
        "windows" => "Windows NT 10.0; Win64; x64",
        _ => "X11; Linux x86_64",
    }
}

pub fn client_build_number() -> u64 {
    PINNED.client_build_number
}

/// A Chrome user agent matching the `os` reported in the super properties.
pub fn user_agent() -> String {
    format!(
        "Mozilla/5.0 ({}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{} Safari/537.36",
        platform_token(),
        PINNED.chrome_version
    )
}

/// Client properties sent in `X-Super-Properties` and gateway Identify.
pub fn super_properties(locale: &str) -> Value {
    json!({
        "os": os_name(),
        "browser": "Chrome",
        "device": "",
        "system_locale": locale,
        "browser_user_agent": user_agent(),
        "browser_version": PINNED.chrome_version,
        "os_version": "",
        "referrer": "",
        "referring_domain": "",
        "referrer_current": "",
        "referring_domain_current": "",
        "release_channel": "stable",
        "client_build_number": PINNED.client_build_number,
        "client_event_source": Value::Null,
    })
}

pub fn encoded_super_properties(locale: &str) -> String {
    STANDARD.encode(super_properties(locale).to_string())
}
