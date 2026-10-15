use serde_json::Value;
use std::path::Path;

use crate::core::settings::AppSettingsManager;
use crate::core::settings::schema::AppSettings;

/// Migrator for `remotes` sub-settings: delegates to `migrate_to_multi_profile`.
pub fn migrate_remote_sub_settings(val: Value) -> Value {
    crate::core::settings::remote::manager::migrate_to_multi_profile(val)
}

/// Creates a new `AppSettingsManager` with all necessary sub-settings.
pub fn create_settings_manager(config_dir: &Path) -> Result<AppSettingsManager, String> {
    let builder =
        rcman::SettingsManager::builder(env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"))
            .with_config_dir(config_dir);
    #[cfg(not(feature = "native-headless"))]
    let builder = builder.with_env_credentials();
    #[cfg(feature = "native-headless")]
    let builder = {
        let source = if let Some(path) = std::env::var_os("RCLONE_MANAGER_SECRET_FILE") {
            rcman::SecretPasswordSource::file(path)
        } else {
            rcman::SecretPasswordSource::env("RCLONE_MANAGER_SECRET")
        };
        let backend = native_credential_backend(config_dir, &source)?;
        builder.with_credential_config(rcman::CredentialConfig::Custom(std::sync::Arc::new(
            backend,
        )))
    };
    builder
        .with_schema::<AppSettings>()
        .with_sub_settings(
            rcman::SubSettingsConfig::new("remotes")
                .with_profiles()
                .with_migrator(migrate_remote_sub_settings),
        )
        .with_sub_settings(rcman::SubSettingsConfig::singlefile("backend").with_profiles())
        .with_sub_settings(
            rcman::SubSettingsConfig::singlefile("connections")
                .with_schema::<crate::rclone::backend::types::Backend>(),
        )
        .with_sub_settings(
            rcman::SubSettingsConfig::singlefile("alerts/rules")
                .with_schema::<crate::core::alerts::types::AlertRule>(),
        )
        .with_sub_settings(
            rcman::SubSettingsConfig::singlefile("alerts/actions")
                .with_schema::<crate::core::alerts::types::AlertAction>(),
        )
        .with_sub_settings(rcman::SubSettingsConfig::singlefile(
            crate::utils::constants::SUB_QUICK_RUNS,
        ))
        .with_sub_settings(rcman::SubSettingsConfig::singlefile(
            crate::utils::constants::SUB_WORKFLOWS,
        ))
        .with_sub_settings(
            rcman::SubSettingsConfig::singlefile(crate::utils::constants::SUB_TEMPLATES)
                .with_schema::<crate::core::settings::schema::UserPresetTemplate>(),
        )
        .build()
        .map_err(|e| format!("Failed to create rcman settings manager: {e}"))
}

#[cfg(feature = "native-headless")]
fn native_credential_backend(
    config_dir: &Path,
    source: &rcman::SecretPasswordSource,
) -> Result<rcman::EncryptedFileBackend, String> {
    let password = source
        .resolve()
        .map_err(|error| format!("Failed to load credential master password: {error}"))?;
    if password.is_empty() {
        return Err("Credential master password must not be empty".to_string());
    }
    rcman::EncryptedFileBackend::with_password(config_dir.join("secrets.enc.json"), &password)
        .map_err(|error| format!("Failed to initialize encrypted credentials: {error}"))
}

#[cfg(all(test, feature = "native-headless"))]
mod native_tests {
    use super::*;
    use rcman::CredentialBackend;

    #[test]
    fn credentials_survive_restart_encrypted_and_reject_wrong_password() {
        let directory = tempfile::tempdir().unwrap();
        let source = rcman::SecretPasswordSource::provided("test master password");
        let backend = native_credential_backend(directory.path(), &source).unwrap();
        backend.store("test-key", "secret-token-value").unwrap();
        drop(backend);
        let persisted = std::fs::read_to_string(directory.path().join("secrets.enc.json")).unwrap();
        assert!(!persisted.contains("secret-token-value"));
        let backend = native_credential_backend(directory.path(), &source).unwrap();
        assert_eq!(
            backend.get("test-key").unwrap().as_deref(),
            Some("secret-token-value")
        );
        assert!(backend.get("missing-key").unwrap().is_none());
        assert!(
            native_credential_backend(
                directory.path(),
                &rcman::SecretPasswordSource::provided("wrong password")
            )
            .is_err()
        );
    }

    #[test]
    fn empty_master_password_returns_error() {
        let directory = tempfile::tempdir().unwrap();
        assert!(
            native_credential_backend(directory.path(), &rcman::SecretPasswordSource::provided(""))
                .is_err()
        );
    }

    #[test]
    fn missing_master_password_file_returns_error() {
        let directory = tempfile::tempdir().unwrap();
        let source = rcman::SecretPasswordSource::file(directory.path().join("missing-master-key"));
        assert!(native_credential_backend(directory.path(), &source).is_err());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_migrate_remote_sub_settings_double_nested() {
        let legacy = json!({
            "runtimeRemoteConfigs": {
                "Default": {
                    "One Drive": {
                        "token": "secret_token",
                        "client_credentials": true
                    }
                }
            }
        });

        let migrated = migrate_remote_sub_settings(legacy);
        let default_profile = &migrated["runtimeRemoteConfigs"]["Default"];

        assert_eq!(default_profile["token"], "secret_token");
        assert_eq!(default_profile["client_credentials"], true);
        assert!(default_profile.get("One Drive").is_none());
    }

    #[test]
    fn test_migrate_remote_sub_settings_already_flat() {
        let flat = json!({
            "runtimeRemoteConfigs": {
                "Default": {
                    "token": "secret_token",
                    "client_credentials": true
                }
            }
        });

        let migrated = migrate_remote_sub_settings(flat.clone());
        assert_eq!(migrated, flat);
    }
}
