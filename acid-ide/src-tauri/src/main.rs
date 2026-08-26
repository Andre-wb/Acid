#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod checker;
mod lsp;
mod project;
mod pty;
mod watcher;

use checker::CheckResponse;
use lsp::{CompletionItem, LspClient};
use project::Entry;
use pty::PtyState;
use std::sync::Arc;
use tauri::{AppHandle, State};
use watcher::WatcherState;

/// Wraps the LSP client in an Arc so it can be cloned into
/// `spawn_blocking` closures (its calls block on the rust-analyzer
/// process, so they must never run directly on a Tokio worker thread).
struct LspState(Arc<LspClient>);

// ---- filesystem / project tree ----------------------------------------

#[tauri::command]
async fn open_project() -> Result<Option<String>, String> {
    let handle = rfd::AsyncFileDialog::new()
        .set_title("Open Rust Project")
        .pick_folder()
        .await;
    Ok(handle.map(|h| h.path().to_string_lossy().to_string()))
}

#[tauri::command]
async fn list_dir(path: String) -> Result<Vec<Entry>, String> {
    project::list_dir(&path)
}

#[tauri::command]
async fn read_file(path: String) -> Result<String, String> {
    project::read_file(&path)
}

#[tauri::command]
async fn write_file(path: String, contents: String) -> Result<(), String> {
    project::write_file(&path, &contents)
}

#[tauri::command]
async fn project_check(root: String) -> Result<CheckResponse, String> {
    checker::check_project(root).await
}

#[tauri::command]
async fn watch_project(root: String, app: AppHandle, state: State<'_, WatcherState>) -> Result<(), String> {
    state.watch(root, app)
}

// ---- rust-analyzer (LSP) -----------------------------------------------

#[tauri::command]
async fn lsp_start(root: String, app: AppHandle, state: State<'_, LspState>) -> Result<(), String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.start(&root, app))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn lsp_did_open(uri: String, text: String, state: State<'_, LspState>) -> Result<(), String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.did_open(&uri, &text))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn lsp_did_change(
    uri: String,
    text: String,
    state: State<'_, LspState>,
    versions: State<'_, dashmap_lite::VersionMap>,
) -> Result<(), String> {
    let version = versions.next(&uri);
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.did_change(&uri, &text, version))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn lsp_did_close(uri: String, state: State<'_, LspState>) -> Result<(), String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.did_close(&uri))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn lsp_completion(
    uri: String,
    line: u32,
    character: u32,
    state: State<'_, LspState>,
) -> Result<Vec<CompletionItem>, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.completion(&uri, line, character))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn lsp_hover(
    uri: String,
    line: u32,
    character: u32,
    state: State<'_, LspState>,
) -> Result<Option<String>, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.hover(&uri, line, character))
        .await
        .map_err(|e| e.to_string())?
}

// ---- terminal (PTY) ------------------------------------------------------

#[tauri::command]
async fn pty_spawn(
    cwd: String,
    cols: u16,
    rows: u16,
    app: AppHandle,
    state: State<'_, PtyState>,
) -> Result<(), String> {
    state.spawn(cwd, cols, rows, app)
}

#[tauri::command]
async fn pty_write(data: String, state: State<'_, PtyState>) -> Result<(), String> {
    state.write(&data)
}

#[tauri::command]
async fn pty_resize(cols: u16, rows: u16, state: State<'_, PtyState>) -> Result<(), String> {
    state.resize(cols, rows)
}

/// Tiny per-document version counter for LSP didChange notifications.
/// Not worth pulling in a whole crate for a HashMap<String, i64> with a
/// mutex, so it lives right here.
mod dashmap_lite {
    use std::collections::HashMap;
    use std::sync::Mutex;

    #[derive(Default)]
    pub struct VersionMap(Mutex<HashMap<String, i64>>);

    impl VersionMap {
        pub fn next(&self, uri: &str) -> i64 {
            let mut map = self.0.lock().unwrap();
            let v = map.entry(uri.to_string()).or_insert(1);
            *v += 1;
            *v
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(LspState(Arc::new(LspClient::new())))
        .manage(PtyState::new())
        .manage(WatcherState::new())
        .manage(dashmap_lite::VersionMap::default())
        .invoke_handler(tauri::generate_handler![
            open_project,
            list_dir,
            read_file,
            write_file,
            project_check,
            watch_project,
            lsp_start,
            lsp_did_open,
            lsp_did_change,
            lsp_did_close,
            lsp_completion,
            lsp_hover,
            pty_spawn,
            pty_write,
            pty_resize,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Acid");
}
