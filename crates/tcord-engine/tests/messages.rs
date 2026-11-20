use serde_json::{Value, json};
use tcord_engine::{EngineError, MessageReference, Session, SessionConfig};
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn message(content: &str) -> Value {
    json!({
        "id": "9", "channel_id": "10",
        "author": {"id": "1", "username": "me"},
        "content": content, "timestamp": "2026-01-01T00:00:00+00:00"
    })
}

async fn session() -> (MockServer, Session) {
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
    let session = Session::new(SessionConfig {
        api_base: Some(server.uri()),
        memory_store: true,
        ..SessionConfig::default()
    })
    .unwrap();
    session
        .auth()
        .login_password("a@b.test", "pw")
        .await
        .unwrap();
    (server, session)
}

async fn bodies(server: &MockServer, route: &str) -> Vec<Value> {
    server
        .received_requests()
        .await
        .unwrap()
        .into_iter()
        .filter(|request| request.url.path() == route)
        .map(|request| serde_json::from_slice(&request.body).unwrap_or(Value::Null))
        .collect()
}

#[tokio::test]
async fn send_reply_sets_nonce_reference_and_mentions() {
    let (server, session) = session().await;
    Mock::given(method("POST"))
        .and(path("/channels/10/messages"))
        .respond_with(ResponseTemplate::new(200).set_body_json(message("hi")))
        .mount(&server)
        .await;
    let reply = MessageReference {
        message_id: "5".into(),
        channel_id: "10".into(),
        guild_id: Some("100".into()),
    };

    session
        .send_message("10", "hi", Some(reply), Some("777".into()))
        .await
        .unwrap();

    let body = &bodies(&server, "/channels/10/messages").await[0];
    assert_eq!(body["nonce"], "777");
    assert_eq!(body["enforce_nonce"], true);
    assert_eq!(body["message_reference"]["message_id"], "5");
    assert_eq!(body["message_reference"]["fail_if_not_exists"], false);
    assert_eq!(body["allowed_mentions"]["replied_user"], true);
}

#[tokio::test]
async fn generated_nonce_is_reused_on_rate_limited_retry() {
    let (server, session) = session().await;
    Mock::given(method("POST"))
        .and(path("/channels/10/messages"))
        .respond_with(ResponseTemplate::new(429).set_body_json(json!({"retry_after": 0.05})))
        .up_to_n_times(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/channels/10/messages"))
        .respond_with(ResponseTemplate::new(200).set_body_json(message("hi")))
        .mount(&server)
        .await;

    session.send_message("10", "hi", None, None).await.unwrap();

    let sent = bodies(&server, "/channels/10/messages").await;
    assert_eq!(sent.len(), 2);
    assert!(sent[0]["nonce"].as_str().unwrap().parse::<u64>().is_ok());
    assert_eq!(sent[0]["nonce"], sent[1]["nonce"]);
}

#[tokio::test]
async fn oversized_content_is_rejected_locally() {
    let (server, session) = session().await;
    let content = "a".repeat(2001);

    let error = session
        .send_message("10", &content, None, None)
        .await
        .unwrap_err();

    assert!(matches!(error, EngineError::InvalidInput(_)));
    assert!(bodies(&server, "/channels/10/messages").await.is_empty());
    assert!(
        session
            .send_message("10", &"é".repeat(2000), None, None)
            .await
            .is_err_and(|error| !matches!(error, EngineError::InvalidInput(_)))
    );
}

#[tokio::test]
async fn edit_delete_and_typing_hit_their_routes() {
    let (server, session) = session().await;
    Mock::given(method("PATCH"))
        .and(path("/channels/10/messages/9"))
        .and(body_partial_json(json!({"content": "edited"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(message("edited")))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("DELETE"))
        .and(path("/channels/10/messages/9"))
        .respond_with(ResponseTemplate::new(204))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/channels/10/typing"))
        .respond_with(ResponseTemplate::new(204))
        .expect(1)
        .mount(&server)
        .await;

    let edited = session.edit_message("10", "9", "edited").await.unwrap();
    session.delete_message("10", "9").await.unwrap();
    session.trigger_typing("10").await.unwrap();

    assert_eq!(edited.content, "edited");
}

#[tokio::test]
async fn ack_sends_previous_token_back() {
    let (server, session) = session().await;
    Mock::given(method("POST"))
        .and(path("/channels/10/messages/9/ack"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"token": "ack-1"})))
        .mount(&server)
        .await;

    session.ack_message("10", "9").await.unwrap();
    session.ack_message("10", "9").await.unwrap();

    let sent = bodies(&server, "/channels/10/messages/9/ack").await;
    assert_eq!(sent[0]["token"], Value::Null);
    assert_eq!(sent[1]["token"], "ack-1");
}
