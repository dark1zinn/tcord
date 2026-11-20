use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use reqwest::Method;
use serde_json::{Value, json};
use tcord_engine::{AuthFailure, AuthKind, DiscordHttp, EngineError, Session, SessionConfig};
use wiremock::matchers::{header_exists, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn retries_short_429_once() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/thing"))
        .respond_with(
            ResponseTemplate::new(429).set_body_json(json!({"retry_after": 0.1, "global": false})),
        )
        .up_to_n_times(1)
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/thing"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"ok": true})))
        .expect(1)
        .mount(&server)
        .await;
    let http = DiscordHttp::new(Some(server.uri())).unwrap();

    let body = http
        .send(Method::GET, "/thing", None, &[], None)
        .await
        .unwrap();

    assert_eq!(body["ok"], true);
}

#[tokio::test]
async fn long_429_is_reported() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/thing"))
        .respond_with(
            ResponseTemplate::new(429).set_body_json(json!({"retry_after": 30.0, "global": false})),
        )
        .expect(1)
        .mount(&server)
        .await;
    let http = DiscordHttp::new(Some(server.uri())).unwrap();

    let error = http
        .send(Method::POST, "/thing", None, &[], Some(&json!({})))
        .await
        .unwrap_err();

    assert_eq!(
        error,
        EngineError::RateLimited {
            retry_after: 30.0,
            global: false
        }
    );
}

#[tokio::test]
async fn captcha_typed() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/thing"))
        .respond_with(ResponseTemplate::new(400).set_body_json(json!({
            "captcha_key": ["captcha-required"],
            "captcha_service": "hcaptcha",
            "captcha_sitekey": "site"
        })))
        .mount(&server)
        .await;
    let http = DiscordHttp::new(Some(server.uri())).unwrap();

    let error = http
        .send(Method::POST, "/thing", None, &[], Some(&json!({})))
        .await
        .unwrap_err();

    assert_eq!(
        error,
        EngineError::CaptchaRequired {
            service: Some("hcaptcha".into()),
            sitekey: Some("site".into())
        }
    );
}

#[tokio::test]
async fn sends_web_client_identity() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/thing"))
        .and(header_exists("x-super-properties"))
        .and(header_exists("x-discord-locale"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({})))
        .mount(&server)
        .await;
    let http = DiscordHttp::with_locale(Some(server.uri()), "pt-BR").unwrap();

    http.send(Method::GET, "/thing", None, &[], None)
        .await
        .unwrap();

    let request = &server.received_requests().await.unwrap()[0];
    let encoded = request.headers["x-super-properties"].to_str().unwrap();
    let properties: Value = serde_json::from_slice(&STANDARD.decode(encoded).unwrap()).unwrap();
    assert!(properties["client_build_number"].as_u64().unwrap() > 0);
    assert_eq!(properties["system_locale"], "pt-BR");
    let agent = request.headers["user-agent"].to_str().unwrap();
    assert!(agent.contains("Chrome/"), "{agent}");
    assert!(properties["browser_user_agent"].as_str().unwrap().eq(agent));
}

#[tokio::test]
async fn unauthorized_invalidates_session() {
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
        .and(path("/users/@me/guilds"))
        .respond_with(ResponseTemplate::new(401).set_body_json(json!({"code": 0})))
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

    let error = session.fetch_guilds().await.unwrap_err();

    assert_eq!(error, EngineError::Unauthorized);
    let snapshot = session.auth().snapshot();
    assert_eq!(snapshot.kind, AuthKind::LoggedOut);
    assert_eq!(snapshot.failure, Some(AuthFailure::SessionExpired));
    assert!(session.auth().token().is_none());
}
