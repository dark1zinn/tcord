use std::sync::Arc;
use std::time::Duration;

use reqwest::Method;
use secrecy::{ExposeSecret, SecretString};
use serde_json::json;
use tcord_engine::{
    AuthController, AuthKind, CredentialStore, DiscordHttp, EngineError, MemoryStore,
};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

async fn controller(server: &MockServer, store: Arc<MemoryStore>) -> AuthController {
    let http = Arc::new(DiscordHttp::new(Some(server.uri())).unwrap());
    AuthController::new(http, store, None)
}

async fn mount_me(server: &MockServer) {
    Mock::given(method("GET"))
        .and(path("/users/@me"))
        .respond_with(
            ResponseTemplate::new(200).set_body_json(json!({"id": "1", "username": "me"})),
        )
        .mount(server)
        .await;
}

#[tokio::test]
async fn login_authenticates_and_persists() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"token": "secret"})))
        .mount(&server)
        .await;
    mount_me(&server).await;
    let store = Arc::new(MemoryStore::default());
    let auth = controller(&server, Arc::clone(&store)).await;

    let snapshot = auth.login_password("a@b.test", "pw").await.unwrap();

    assert_eq!(snapshot.kind, AuthKind::Authenticated);
    assert_eq!(snapshot.user.unwrap().username, "me");
    assert!(snapshot.session_persisted);
    assert_eq!(store.load().unwrap().unwrap().expose_secret(), "secret");
}

#[tokio::test]
async fn logout_during_login_stays_logged_out() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({"token": "secret"}))
                .set_delay(Duration::from_millis(300)),
        )
        .mount(&server)
        .await;
    mount_me(&server).await;
    let auth = Arc::new(controller(&server, Arc::new(MemoryStore::default())).await);

    let login = tokio::spawn({
        let auth = Arc::clone(&auth);
        async move { auth.login_password("a@b.test", "pw").await }
    });
    tokio::time::sleep(Duration::from_millis(50)).await;
    auth.logout().await;
    login.await.unwrap().unwrap();
    tokio::time::sleep(Duration::from_millis(400)).await;

    assert_eq!(auth.snapshot().kind, AuthKind::LoggedOut);
    assert!(auth.token().is_none());
}

#[tokio::test]
async fn logout_clears_token_for_rest() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"token": "secret"})))
        .mount(&server)
        .await;
    mount_me(&server).await;
    Mock::given(method("GET"))
        .and(path("/users/@me/guilds"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([])))
        .expect(0)
        .mount(&server)
        .await;
    let store = Arc::new(MemoryStore::default());
    let auth = controller(&server, Arc::clone(&store)).await;
    auth.login_password("a@b.test", "pw").await.unwrap();

    let snapshot = auth.logout().await;

    assert_eq!(snapshot.kind, AuthKind::LoggedOut);
    let token = auth.token();
    assert!(token.is_none());
    assert!(store.load().unwrap().is_none());
    let http = DiscordHttp::new(Some(server.uri())).unwrap();
    let result = match token {
        Some(token) => {
            http.send(Method::GET, "/users/@me/guilds", Some(&token), &[], None)
                .await
        }
        None => Err(EngineError::Unauthorized),
    };
    assert_eq!(result.unwrap_err(), EngineError::Unauthorized);
}

#[tokio::test]
async fn resume_with_revoked_token_deletes_it() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/users/@me"))
        .respond_with(ResponseTemplate::new(401).set_body_json(json!({"code": 0})))
        .mount(&server)
        .await;
    let store = Arc::new(MemoryStore::default());
    store.save(&SecretString::from("old".to_owned())).unwrap();
    let auth = controller(&server, Arc::clone(&store)).await;

    let snapshot = auth.resume().await;

    assert_eq!(snapshot.kind, AuthKind::WaitingForCredentials);
    assert!(store.load().unwrap().is_none());
}

#[tokio::test]
async fn memory_store_round_trip() {
    let store = MemoryStore::default();
    assert!(store.load().unwrap().is_none());
    store.save(&SecretString::from("t".to_owned())).unwrap();
    assert_eq!(store.load().unwrap().unwrap().expose_secret(), "t");
    store.delete().unwrap();
    assert!(store.load().unwrap().is_none());
}
