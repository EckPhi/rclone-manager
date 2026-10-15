//! Exercise the same HTTP contract consumed by the browser without Tauri.
use super::*;
use crate::runtime::Manager;
use futures::StreamExt;
use serde_json::{Value, json};
use std::time::Duration;

struct TestServer {
    url: String,
    task: tokio::task::JoinHandle<()>,
    bridge: Arc<EventBridge>,
    _directory: tempfile::TempDir,
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl TestServer {
    async fn start() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let browser = directory.path().join("browser");
        std::fs::create_dir(&browser).unwrap();
        std::fs::write(browser.join("index.html"), "<html>native browser</html>").unwrap();
        let app = AppHandle::new();
        let config = rcman::SettingsConfig::builder("native-http-test", "1.0.0")
            .with_config_dir(directory.path())
            .with_schema::<crate::core::settings::schema::AppSettings>()
            .build();
        let settings: crate::core::settings::AppSettingsManager =
            rcman::SettingsManager::new(config).unwrap();
        app.manage(settings);
        app.manage(crate::utils::types::provision::ProvisionState::default());
        app.manage(tokio::sync::Mutex::new(
            crate::utils::types::state::RcApiEngine::default(),
        ));
        let bridge = Arc::new(EventBridge::new(16));
        let state = WebServerState {
            app_handle: app,
            event_bridge: bridge.clone(),
            auth_credentials: Some(("admin".into(), STANDARD.encode("admin:test-password"))),
            sessions: Arc::new(RwLock::new(HashSet::new())),
        };
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let router = build_app(state, Some(browser), "127.0.0.1", address.port());
        let task = crate::utils::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        });
        Self {
            url: format!("http://{address}"),
            task,
            bridge,
            _directory: directory,
        }
    }

    fn authenticated(&self, path: &str) -> reqwest::RequestBuilder {
        reqwest::Client::new()
            .post(format!("{}{path}", self.url))
            .basic_auth("admin", Some("test-password"))
    }

    async fn invoke(&self, command: &str, args: Value) -> Value {
        self.authenticated("/api/invoke")
            .json(&json!({"command": command, "args": args}))
            .send()
            .await
            .unwrap()
            .error_for_status()
            .unwrap()
            .json()
            .await
            .unwrap()
    }
}

#[tokio::test]
async fn native_http_preserves_browser_settings_status_and_error_contracts() {
    let server = TestServer::start().await;
    let client = reqwest::Client::new();
    assert!(
        client
            .get(format!("{}/health", server.url))
            .send()
            .await
            .unwrap()
            .status()
            .is_success()
    );
    let page = client
        .get(format!("{}/nautilus/test", server.url))
        .send()
        .await
        .unwrap()
        .text()
        .await
        .unwrap();
    assert_eq!(page, "<html>native browser</html>");
    let saved = server
        .invoke(
            "save_setting",
            json!({"category": "developer", "key": "log_level", "value": "debug"}),
        )
        .await;
    assert_eq!(saved["success"], true);
    let settings = server.invoke("load_settings", json!({})).await;
    assert_eq!(settings["success"], true);
    assert_eq!(
        settings["data"]["options"]["developer.log_level"]["value"],
        "debug"
    );
    let status = server.invoke("get_system_status_snapshot", json!({})).await;
    assert_eq!(status["data"]["status"], "inactive");
    let provision = server.invoke("get_provision_status", json!({})).await;
    assert_eq!(provision["success"], true);
    let invalid = server
        .authenticated("/api/invoke")
        .json(&json!({"command":"save_setting", "args": {}}))
        .send()
        .await
        .unwrap();
    assert_eq!(invalid.status(), reqwest::StatusCode::INTERNAL_SERVER_ERROR);
    let invalid: Value = invalid.json().await.unwrap();
    assert_eq!(invalid["success"], false);
    assert!(invalid["error"].as_str().unwrap().contains("missing field"));
    let unknown = server
        .authenticated("/api/invoke")
        .json(&json!({"command":"not_a_command", "args": {}}))
        .send()
        .await
        .unwrap();
    assert_eq!(unknown.status(), reqwest::StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn native_http_requires_auth_and_session_logout_revokes_access() {
    let server = TestServer::start().await;
    let client = reqwest::Client::new();
    for path in ["/api/invoke", "/api/auth/session"] {
        let response = client
            .post(format!("{}{path}", server.url))
            .json(&json!({}))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), reqwest::StatusCode::UNAUTHORIZED);
    }
    let rejected = client
        .post(format!("{}/api/auth/session", server.url))
        .basic_auth("admin", Some("incorrect-password"))
        .send()
        .await
        .unwrap();
    assert_eq!(rejected.status(), reqwest::StatusCode::UNAUTHORIZED);
    let session = server
        .authenticated("/api/auth/session")
        .send()
        .await
        .unwrap();
    let cookie = session.headers()[reqwest::header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    let response = client
        .post(format!("{}/api/invoke", server.url))
        .header(reqwest::header::COOKIE, &cookie)
        .json(&json!({"command":"get_provision_status", "args": {}}))
        .send()
        .await
        .unwrap();
    assert!(response.status().is_success());
    let logout = client
        .delete(format!("{}/api/auth/session", server.url))
        .header(reqwest::header::COOKIE, &cookie)
        .send()
        .await
        .unwrap();
    assert!(logout.status().is_success());
    let response = client
        .get(format!("{}/api/events", server.url))
        .header(reqwest::header::COOKIE, cookie)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn native_sse_preserves_event_name_and_payload() {
    let server = TestServer::start().await;
    let response = reqwest::Client::new()
        .get(format!("{}/api/events", server.url))
        .basic_auth("admin", Some("test-password"))
        .send()
        .await
        .unwrap();
    assert_eq!(
        response.headers()[reqwest::header::CONTENT_TYPE],
        "text/event-stream"
    );
    let mut stream = response.bytes_stream();
    server.bridge.emit(
        crate::utils::types::events::REMOTE_CACHE_CHANGED,
        "test-remote",
    );
    let bytes = tokio::time::timeout(Duration::from_secs(2), stream.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let frame = std::str::from_utf8(&bytes).unwrap();
    let event: Value = serde_json::from_str(
        frame
            .trim()
            .strip_prefix("data: ")
            .or_else(|| frame.trim().strip_prefix("data:"))
            .unwrap(),
    )
    .unwrap();
    assert_eq!(
        event["event"],
        crate::utils::types::events::REMOTE_CACHE_CHANGED
    );
    assert_eq!(event["payload"], "test-remote");
}
