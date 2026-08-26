use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Emitter};

/// Watches the currently open project root and tells the frontend
/// "something changed, re-list whatever directories you have expanded"
/// — used so files created from the embedded terminal (e.g. `touch
/// foo.rs`, `cargo new`) show up in the sidebar without a manual
/// refresh. We don't try to diff exactly what changed; the frontend
/// just re-lists the directories it already has open, which is cheap.
pub struct WatcherState(Mutex<Option<RecommendedWatcher>>);

impl WatcherState {
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }

    pub fn watch(&self, root: String, app: AppHandle) -> Result<(), String> {
        let (tx, rx) = std::sync::mpsc::channel::<notify::Result<notify::Event>>();

        let mut watcher = notify::recommended_watcher(move |res| {
            let _ = tx.send(res);
        })
        .map_err(|e| e.to_string())?;

        watcher
            .watch(Path::new(&root), RecursiveMode::Recursive)
            .map_err(|e| e.to_string())?;

        // Dropping the watcher stops it, so it has to live somewhere —
        // replacing the old one here also stops watching the previous
        // project automatically.
        *self.0.lock().unwrap() = Some(watcher);

        std::thread::spawn(move || loop {
            match rx.recv() {
                Ok(_) => {
                    // A single `cargo build` or git checkout fires dozens
                    // of events in a burst — drain whatever else shows up
                    // in the next 150ms so we emit one refresh, not fifty.
                    while rx.recv_timeout(Duration::from_millis(150)).is_ok() {}
                    if app.emit("project://fs-changed", ()).is_err() {
                        break;
                    }
                }
                Err(_) => break, // watcher was dropped (project closed/switched)
            }
        });

        Ok(())
    }
}
