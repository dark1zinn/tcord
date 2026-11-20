use std::sync::Arc;

use serde_json::{Value, json};
use tcord_engine::{
    AuthController, AuthFailure, AuthKind, DiscordHttp, EngineError, MemoryStore, MfaMethod,
};
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

async fn setup(login_status: u16, login_body: Value) -> (MockServer, AuthController) {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(ResponseTemplate::new(login_status).set_body_json(login_body))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/users/@me"))
        .respond_with(
            ResponseTemplate::new(200).set_body_json(json!({"id": "1", "username": "me"})),
        )
        .mount(&server)
        .await;
    let http = Arc::new(DiscordHttp::new(Some(server.uri())).unwrap());
    let auth = AuthController::new(http, Arc::new(MemoryStore::default()), None);
    (server, auth)
}

fn mfa_body() -> Value {
    json!({
        "user_id": "1", "mfa": true, "ticket": "T", "login_instance_id": "I",
        "totp": true, "sms": true, "backup": true, "webauthn": null
    })
}

#[tokio::test]
async fn mfa_challenge_lists_methods_in_order() {
    let (_server, auth) = setup(200, mfa_body()).await;
    let snapshot = auth.login_password("a@b.test", "pw").await.unwrap();
    assert_eq!(snapshot.kind, AuthKind::WaitingForMfa);
    assert_eq!(
        snapshot.mfa_methods,
        vec![MfaMethod::Totp, MfaMethod::Sms, MfaMethod::Backup]
    );
}

#[tokio::test]
async fn wrong_code_stays_waiting_for_mfa() {
    let (server, auth) = setup(200, mfa_body()).await;
    Mock::given(method("POST"))
        .and(path("/auth/mfa/totp"))
        .respond_with(
            ResponseTemplate::new(400).set_body_json(json!({"code": 60008, "message": "Invalid"})),
        )
        .mount(&server)
        .await;
    auth.login_password("a@b.test", "pw").await.unwrap();

    let snapshot = auth.submit_mfa(MfaMethod::Totp, "000000").await.unwrap();

    assert_eq!(snapshot.kind, AuthKind::WaitingForMfa);
    assert_eq!(snapshot.failure, Some(AuthFailure::InvalidCode));
    assert_eq!(snapshot.mfa_methods.len(), 3);
}

#[tokio::test]
async fn backup_code_completes_login() {
    let (server, auth) = setup(200, mfa_body()).await;
    Mock::given(method("POST"))
        .and(path("/auth/mfa/backup"))
        .and(body_partial_json(
            json!({"ticket": "T", "code": "abcd-efgh", "login_instance_id": "I"}),
        ))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"token": "tok"})))
        .mount(&server)
        .await;
    auth.login_password("a@b.test", "pw").await.unwrap();

    let snapshot = auth
        .submit_mfa(MfaMethod::Backup, "abcd-efgh")
        .await
        .unwrap();

    assert_eq!(snapshot.kind, AuthKind::Authenticated);
}

#[tokio::test]
async fn sms_send_returns_redacted_phone() {
    let (server, auth) = setup(200, mfa_body()).await;
    Mock::given(method("POST"))
        .and(path("/auth/mfa/sms/send"))
        .and(body_partial_json(json!({"ticket": "T"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"phone": "+*******0085"})))
        .mount(&server)
        .await;
    auth.login_password("a@b.test", "pw").await.unwrap();

    assert_eq!(
        auth.send_mfa_sms().await.unwrap().as_deref(),
        Some("+*******0085")
    );
}

#[tokio::test]
async fn submitting_without_challenge_is_rejected() {
    let (_server, auth) = setup(200, json!({"token": "tok"})).await;
    let error = auth
        .submit_mfa(MfaMethod::Totp, "123456")
        .await
        .unwrap_err();
    assert!(matches!(error, EngineError::InvalidInput(_)));
}

#[tokio::test]
async fn webauthn_only_is_unsupported() {
    let body = json!({"mfa": true, "ticket": "T", "webauthn": "{}"});
    let (_server, auth) = setup(200, body).await;
    let snapshot = auth.login_password("a@b.test", "pw").await.unwrap();
    assert_eq!(snapshot.kind, AuthKind::Failed);
    assert_eq!(snapshot.failure, Some(AuthFailure::WebauthnUnsupported));
}

#[tokio::test]
async fn login_error_codes_map_to_failures() {
    let cases = [
        (
            400,
            json!({"code": 70007, "message": "phone"}),
            AuthKind::Failed,
            AuthFailure::PhoneVerificationRequired,
        ),
        (
            400,
            json!({"code": 20013, "message": "disabled"}),
            AuthKind::Failed,
            AuthFailure::AccountDisabled,
        ),
        (
            400,
            json!({"captcha_key": ["captcha-required"], "captcha_service": "hcaptcha"}),
            AuthKind::WaitingForCredentials,
            AuthFailure::CaptchaRequired,
        ),
        (
            400,
            json!({"code": 50035, "errors": {"login": {"_errors": [{"code": "INVALID_LOGIN"}]}}}),
            AuthKind::WaitingForCredentials,
            AuthFailure::InvalidCredentials,
        ),
        (
            429,
            json!({"retry_after": 12.5, "global": false}),
            AuthKind::WaitingForCredentials,
            AuthFailure::RateLimited,
        ),
    ];
    for (status, body, kind, failure) in cases {
        let (_server, auth) = setup(status, body).await;
        let snapshot = auth.login_password("a@b.test", "pw").await.unwrap();
        assert_eq!(snapshot.kind, kind, "{failure:?}");
        assert_eq!(snapshot.failure, Some(failure));
    }
}
