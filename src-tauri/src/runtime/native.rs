use parking_lot::RwLock;
use std::{
    any::{Any, TypeId},
    collections::HashMap,
    marker::PhantomData,
    ops::Deref,
    path::PathBuf,
    sync::Arc,
};
use tokio::sync::watch;

/// Shared server state. Values remain owned until the last application handle is dropped.
pub trait Runtime: Clone + Send + Sync + 'static {}
#[derive(Clone)]
pub struct NativeRuntime;
impl Runtime for NativeRuntime {}

#[derive(Clone)]
pub struct AppHandle<R: Runtime = NativeRuntime> {
    state: Arc<RwLock<HashMap<TypeId, Arc<dyn Any + Send + Sync>>>>,
    exit: watch::Sender<Option<i32>>,
    runtime: PhantomData<R>,
}
pub type App = AppHandle;

pub struct State<'a, T> {
    value: Arc<T>,
    lifetime: PhantomData<&'a T>,
}
impl<T> Clone for State<'_, T> {
    fn clone(&self) -> Self {
        Self {
            value: self.value.clone(),
            lifetime: PhantomData,
        }
    }
}
impl<T> State<'_, T> {
    pub fn inner(&self) -> &T {
        &self.value
    }
}
impl<T> Deref for State<'_, T> {
    type Target = T;
    fn deref(&self) -> &T {
        self.inner()
    }
}

pub trait Manager {
    fn manage<T: Send + Sync + 'static>(&self, value: T) -> bool;
    fn try_state<T: Send + Sync + 'static>(&self) -> Option<State<'_, T>>;
    fn state<T: Send + Sync + 'static>(&self) -> State<'_, T> {
        self.try_state()
            .expect("application state has not been initialized")
    }
    fn path(&self) -> path::PathResolver;
}
impl<R: Runtime> Manager for AppHandle<R> {
    fn manage<T: Send + Sync + 'static>(&self, value: T) -> bool {
        let mut state = self.state.write();
        if state.contains_key(&TypeId::of::<T>()) {
            return false;
        }
        state.insert(TypeId::of::<T>(), Arc::new(value));
        true
    }
    fn try_state<T: Send + Sync + 'static>(&self) -> Option<State<'_, T>> {
        self.state
            .read()
            .get(&TypeId::of::<T>())
            .cloned()?
            .downcast()
            .ok()
            .map(|value| State {
                value,
                lifetime: PhantomData,
            })
    }
    fn path(&self) -> path::PathResolver {
        path::PathResolver
    }
}
impl<R: Runtime> Default for AppHandle<R> {
    fn default() -> Self {
        Self::new()
    }
}
impl<R: Runtime> AppHandle<R> {
    pub fn new() -> Self {
        let (exit, _) = watch::channel(None);
        Self {
            state: Arc::new(RwLock::new(HashMap::new())),
            exit,
            runtime: PhantomData,
        }
    }
    pub fn handle(&self) -> &Self {
        self
    }
    pub fn exit(&self, code: i32) {
        self.exit.send_replace(Some(code));
    }
    pub async fn wait_for_exit(&self) -> i32 {
        let mut receiver = self.exit.subscribe();
        loop {
            if let Some(code) = *receiver.borrow_and_update() {
                return code;
            }
            if receiver.changed().await.is_err() {
                return 1;
            }
        }
    }
}

pub mod path {
    use super::PathBuf;
    use std::path::Path;
    pub enum BaseDirectory {
        Resource,
    }
    pub struct PathResolver;
    fn resource_directory_for_executable(executable: &Path) -> std::io::Result<PathBuf> {
        executable
            .parent()
            .and_then(Path::parent)
            .map(PathBuf::from)
            .ok_or_else(|| std::io::Error::other("executable has no resource directory"))
    }
    impl PathResolver {
        pub fn resource_dir(&self) -> std::io::Result<PathBuf> {
            if let Some(path) = std::env::var_os("RCLONE_MANAGER_RESOURCE_DIR") {
                return Ok(path.into());
            }
            resource_directory_for_executable(&std::env::current_exe()?)
        }
        pub fn resolve(&self, path: &str, _base: BaseDirectory) -> std::io::Result<PathBuf> {
            Ok(self.resource_dir()?.join(path))
        }
        pub fn app_data_dir(&self) -> std::io::Result<PathBuf> {
            std::env::var_os("RCLONE_MANAGER_DATA_DIR")
                .map(PathBuf::from)
                .map(Ok)
                .unwrap_or_else(|| Ok(self.resource_dir()?.join("data")))
        }
        pub fn app_cache_dir(&self) -> std::io::Result<PathBuf> {
            std::env::var_os("RCLONE_MANAGER_CACHE_DIR")
                .map(PathBuf::from)
                .map(Ok)
                .unwrap_or_else(|| Ok(self.app_data_dir()?.join("cache")))
        }
        #[cfg(target_os = "macos")]
        pub fn app_log_dir(&self) -> std::io::Result<PathBuf> {
            Ok(self.app_data_dir()?.join("logs"))
        }
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn packaged_binary_resolves_resources_at_package_root() {
            assert_eq!(
                resource_directory_for_executable(Path::new(
                    "/var/packages/rclone-manager/target/bin/rclone-manager-server"
                ))
                .unwrap(),
                PathBuf::from("/var/packages/rclone-manager/target")
            );
        }
        #[test]
        fn invalid_executable_layout_returns_error() {
            assert!(resource_directory_for_executable(Path::new("server")).is_err());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn managed_state_is_shared_and_does_not_replace_existing_values() {
        let app = App::new();
        assert!(app.try_state::<String>().is_none());
        assert!(app.manage(String::from("original")));
        assert!(!app.manage(String::from("replacement")));
        assert_eq!(app.clone().state::<String>().as_str(), "original");
        assert!(app.manage(42_u32));
        assert_eq!(*app.state::<u32>(), 42);
        assert!(app.manage(std::sync::atomic::AtomicUsize::new(0)));
        let state = app.state::<std::sync::atomic::AtomicUsize>();
        let cloned = state.clone();
        cloned.store(5, std::sync::atomic::Ordering::SeqCst);
        assert_eq!(state.load(std::sync::atomic::Ordering::SeqCst), 5);
    }
    #[tokio::test]
    async fn exit_is_retained_before_and_after_subscribing() {
        let app = App::new();
        app.exit(7);
        assert_eq!(app.wait_for_exit().await, 7);
        let app = App::new();
        let handle = app.clone();
        let waiter = crate::utils::spawn(async move { handle.wait_for_exit().await });
        app.exit(0);
        assert_eq!(waiter.await.unwrap(), 0);
    }
}
