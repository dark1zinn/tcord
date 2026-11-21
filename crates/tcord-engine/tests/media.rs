//! Media downloads, the disk cache, thread search and rich message parsing.

use serde_json::json;
use tcord_engine::{EngineError, HostPolicy, Message, Session, SessionConfig};
use wiremock::matchers::{method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn session(api: &MockServer, cache: Option<std::path::PathBuf>, hosts: HostPolicy) -> Session {
    Session::new(SessionConfig {
        api_base: Some(api.uri()),
        memory_store: true,
        cache_dir: cache,
        media_hosts: Some(hosts),
        ..SessionConfig::default()
    })
    .unwrap()
}

#[tokio::test]
async fn media_is_cached_on_disk_ignoring_signature_params() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/attachments/1/2/a.png"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(b"PNGDATA".to_vec()))
        .expect(1)
        .mount(&server)
        .await;
    let dir = std::env::temp_dir().join(format!("tcord-media-{}", std::process::id()));
    let session = session(&server, Some(dir.clone()), HostPolicy::Any);

    let first = format!("{}/attachments/1/2/a.png?ex=1&is=2&hm=3", server.uri());
    let refreshed = format!("{}/attachments/1/2/a.png?ex=9&is=8&hm=7", server.uri());
    assert_eq!(session.fetch_media(&first).await.unwrap(), b"PNGDATA");
    assert_eq!(session.fetch_media(&refreshed).await.unwrap(), b"PNGDATA");
    let _ = std::fs::remove_dir_all(dir);
}

#[tokio::test]
async fn non_discord_hosts_are_refused_without_a_request() {
    let server = MockServer::start().await;
    let session = session(&server, None, HostPolicy::Discord);

    let error = session
        .fetch_media(&format!("{}/a.png", server.uri()))
        .await
        .unwrap_err();

    assert!(matches!(error, EngineError::InvalidInput(_)));
    assert!(server.received_requests().await.unwrap().is_empty());
}

#[tokio::test]
async fn missing_media_maps_to_not_found() {
    let server = MockServer::start().await;
    let session = session(&server, None, HostPolicy::Any);
    let error = session
        .fetch_media(&format!("{}/gone.png", server.uri()))
        .await
        .unwrap_err();
    assert!(matches!(error, EngineError::NotFound));
}

#[tokio::test]
async fn thread_search_marks_joined_threads() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"token": "tok"})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/users/@me"))
        .respond_with(
            ResponseTemplate::new(200).set_body_json(json!({"id": "1", "username": "me"})),
        )
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/channels/10/threads/search"))
        .and(query_param("archived", "false"))
        .and(query_param("sort_by", "last_message_time"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "threads": [
                {"id": "30", "type": 11, "parent_id": "10", "name": "a"},
                {"id": "31", "type": 11, "parent_id": "10", "name": "b"}
            ],
            "members": [{"id": "31", "user_id": "1"}],
            "has_more": true
        })))
        .mount(&server)
        .await;
    let session = session(&server, None, HostPolicy::Any);
    session
        .auth()
        .login_password("a@b.test", "pw")
        .await
        .unwrap();

    let page = session.search_threads("10", false, 0).await.unwrap();

    assert!(page.has_more);
    let joined: Vec<bool> = page.threads.iter().map(|thread| thread.joined).collect();
    assert_eq!(joined, [false, true]);
}

#[test]
fn rich_message_fields_parse() {
    let message = Message::from_value(&json!({
        "id": "5", "channel_id": "10", "guild_id": "100", "type": 0,
        "author": {"id": "2", "username": "friend", "avatar": "a_hash"},
        "member": {"nick": "Pal", "roles": ["7"], "avatar": "guildhash"},
        "content": "look", "timestamp": "2026-01-01T00:00:00+00:00",
        "attachments": [{
            "id": "1", "filename": "cat.png", "url": "https://cdn.discordapp.com/x",
            "proxy_url": "https://media.discordapp.net/x", "size": 10,
            "content_type": "image/png", "width": 640, "height": 480
        }],
        "embeds": [{"type": "gifv", "url": "https://tenor.com/x",
            "thumbnail": {"url": "https://t", "proxy_url": "https://images-ext-1.discordapp.net/t",
                          "width": 200, "height": 100},
            "video": {"url": "https://v"}}],
        "sticker_items": [{"id": "8", "name": "wave", "format_type": 4}],
        "thread": {"id": "300", "type": 11, "parent_id": "10", "message_count": 4}
    }))
    .unwrap();
    assert_eq!(message.author.avatar.as_deref(), Some("a_hash"));
    let member = message.member.unwrap();
    assert_eq!(
        (member.nick.as_deref(), member.roles.as_slice()),
        (Some("Pal"), &["7".to_owned()][..])
    );
    assert_eq!(message.attachments[0].width, Some(640));
    assert_eq!(message.embeds[0].kind, "gifv");
    assert_eq!(
        message.embeds[0].thumbnail.as_ref().unwrap().width,
        Some(200)
    );
    assert_eq!(message.stickers[0].format, 4);
    assert_eq!(message.thread.unwrap().message_count, Some(4));
}
