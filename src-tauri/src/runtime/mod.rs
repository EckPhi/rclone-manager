//! Runtime boundary: the native server owns managed state without a GUI event loop.
#[cfg(not(feature = "native-headless"))]
pub use tauri::{App, AppHandle, Manager, Runtime, State, path};

#[cfg(feature = "native-headless")]
mod native;
#[cfg(feature = "native-headless")]
pub use native::{App, AppHandle, Manager, Runtime, State, path};
