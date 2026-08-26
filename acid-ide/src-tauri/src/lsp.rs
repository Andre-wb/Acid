use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

/// Minimal JSON-RPC client for rust-analyzer. Deliberately hand-rolled
/// instead of pulling in a full LSP framework: rust-analyzer only needs
/// a handful of methods here (initialize, didOpen/didChange, completion,
/// hover) and publishDiagnostics notifications forwarded to the UI.
pub struct LspClient {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<std::process::ChildStdin>>,
    next_id: AtomicI64,
    pending: Arc<Mutex<HashMap<i64, std::sync::mpsc::Sender<Value>>>>,
}

#[derive(Debug, Serialize)]
pub struct CompletionItem {
    pub label: String,
    pub kind: String,
    pub detail: Option<String>,
    pub insert_text: String,
}

impl LspClient {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            stdin: Mutex::new(None),
            next_id: AtomicI64::new(1),
            pending: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Spawns rust-analyzer, performs the initialize/initialized
    /// handshake, and starts a background thread that reads its stdout
    /// forever, routing responses to whoever is waiting and forwarding
    /// diagnostics notifications straight to the frontend as events.
    pub fn start(&self, root: &str, app: AppHandle) -> Result<(), String> {
        // If a previous project is open, tear it down first.
        self.shutdown();

        let mut child = Command::new("rust-analyzer")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| {
                format!("couldn't start rust-analyzer (is it installed and on PATH?): {e}")
            })?;

        let stdin = child.stdin.take().ok_or("rust-analyzer: no stdin")?;
        let stdout = child.stdout.take().ok_or("rust-analyzer: no stdout")?;

        *self.stdin.lock().unwrap() = Some(stdin);
        *self.child.lock().unwrap() = Some(child);

        let pending = self.pending.clone();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                match read_message(&mut reader) {
                    Ok(Some(value)) => {
                        let is_response = value.get("id").is_some() && value.get("method").is_none();
                        if is_response {
                            if let Some(id) = value.get("id").and_then(|v| v.as_i64()) {
                                if let Some(tx) = pending.lock().unwrap().remove(&id) {
                                    let _ = tx.send(value);
                                }
                            }
                            continue;
                        }

                        if let Some(method) = value.get("method").and_then(|m| m.as_str()) {
                            if method == "textDocument/publishDiagnostics" {
                                if let Some(params) = value.get("params").cloned() {
                                    let _ = app.emit("rust-analyzer://diagnostics", params);
                                }
                            }
                        }
                    }
                    Ok(None) => break, // stdout closed, rust-analyzer exited
                    Err(_) => break,
                }
            }
        });

        let root_uri = path_to_uri(root);
        let init_params = json!({
            "processId": std::process::id(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "completion": { "completionItem": { "snippetSupport": false } },
                    "hover": { "contentFormat": ["markdown", "plaintext"] },
                    "publishDiagnostics": {}
                },
                "workspace": { "workspaceFolders": true }
            },
            "workspaceFolders": [{ "uri": root_uri, "name": "workspace" }]
        });

        self.send_request("initialize", init_params)?;
        self.send_notification("initialized", json!({}))?;
        Ok(())
    }

    pub fn did_open(&self, uri: &str, text: &str) -> Result<(), String> {
        self.send_notification(
            "textDocument/didOpen",
            json!({
                "textDocument": { "uri": uri, "languageId": "rust", "version": 1, "text": text }
            }),
        )
    }

    pub fn did_change(&self, uri: &str, text: &str, version: i64) -> Result<(), String> {
        self.send_notification(
            "textDocument/didChange",
            json!({
                "textDocument": { "uri": uri, "version": version },
                "contentChanges": [{ "text": text }]
            }),
        )
    }

    pub fn did_close(&self, uri: &str) -> Result<(), String> {
        self.send_notification(
            "textDocument/didClose",
            json!({ "textDocument": { "uri": uri } }),
        )
    }

    pub fn completion(&self, uri: &str, line: u32, character: u32) -> Result<Vec<CompletionItem>, String> {
        let value = self.send_request(
            "textDocument/completion",
            json!({
                "textDocument": { "uri": uri },
                "position": { "line": line, "character": character }
            }),
        )?;
        Ok(map_completion(value))
    }

    pub fn hover(&self, uri: &str, line: u32, character: u32) -> Result<Option<String>, String> {
        let value = self.send_request(
            "textDocument/hover",
            json!({
                "textDocument": { "uri": uri },
                "position": { "line": line, "character": character }
            }),
        )?;
        Ok(map_hover(value))
    }

    pub fn shutdown(&self) {
        let _ = self.send_notification("exit", json!({}));
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
        }
        *self.stdin.lock().unwrap() = None;
    }

    fn next_id(&self) -> i64 {
        self.next_id.fetch_add(1, Ordering::SeqCst)
    }

    fn send_request(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id();
        let (tx, rx) = std::sync::mpsc::channel::<Value>();
        self.pending.lock().unwrap().insert(id, tx);

        let msg = json!({ "jsonrpc": "2.0", "id": id, "method": method, "params": params });
        {
            let mut guard = self.stdin.lock().unwrap();
            let stdin = guard.as_mut().ok_or("rust-analyzer is not running")?;
            write_message(stdin, &msg).map_err(|e| e.to_string())?;
        }

        rx.recv_timeout(Duration::from_secs(30))
            .map_err(|_| format!("rust-analyzer timed out responding to {method}"))
    }

    fn send_notification(&self, method: &str, params: Value) -> Result<(), String> {
        let msg = json!({ "jsonrpc": "2.0", "method": method, "params": params });
        let mut guard = self.stdin.lock().unwrap();
        let stdin = guard.as_mut().ok_or("rust-analyzer is not running")?;
        write_message(stdin, &msg).map_err(|e| e.to_string())
    }
}

fn write_message<W: Write>(w: &mut W, value: &Value) -> std::io::Result<()> {
    let body = serde_json::to_vec(value).unwrap();
    write!(w, "Content-Length: {}\r\n\r\n", body.len())?;
    w.write_all(&body)?;
    w.flush()
}

/// Reads one LSP frame: `Content-Length: N\r\n\r\n<N bytes of JSON>`.
fn read_message<R: BufRead>(r: &mut R) -> std::io::Result<Option<Value>> {
    let mut content_length: Option<usize> = None;

    loop {
        let mut line = String::new();
        let n = r.read_line(&mut line)?;
        if n == 0 {
            return Ok(None); // EOF
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            break; // blank line ends the headers
        }
        if let Some(rest) = trimmed.strip_prefix("Content-Length:") {
            content_length = rest.trim().parse::<usize>().ok();
        }
    }

    let len = content_length.ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidData, "missing Content-Length header")
    })?;

    let mut buf = vec![0u8; len];
    r.read_exact(&mut buf)?;
    serde_json::from_slice(&buf)
        .map(Some)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}

fn path_to_uri(path: &str) -> String {
    if path.starts_with('/') {
        format!("file://{path}")
    } else {
        // best-effort for Windows-style paths
        format!("file:///{}", path.replace('\\', "/"))
    }
}

fn map_completion(value: Value) -> Vec<CompletionItem> {
    let items: Vec<Value> = if let Some(arr) = value.get("result").and_then(|r| r.as_array()) {
        arr.clone()
    } else if let Some(items) = value
        .get("result")
        .and_then(|r| r.get("items"))
        .and_then(|i| i.as_array())
    {
        items.clone()
    } else {
        Vec::new()
    };

    items
        .into_iter()
        .map(|it| {
            let label = it.get("label").and_then(|l| l.as_str()).unwrap_or("").to_string();
            let kind_num = it.get("kind").and_then(|k| k.as_i64()).unwrap_or(0);
            let detail = it.get("detail").and_then(|d| d.as_str()).map(|s| s.to_string());
            let insert_text = it
                .get("insertText")
                .and_then(|s| s.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| label.clone());

            CompletionItem {
                label,
                kind: completion_kind_name(kind_num),
                detail,
                insert_text,
            }
        })
        .collect()
}

/// LSP `CompletionItemKind` numeric values, mapped to names Monaco
/// understands on the frontend side.
fn completion_kind_name(k: i64) -> String {
    match k {
        2 => "method",
        3 => "function",
        5 => "field",
        6 => "variable",
        7 => "class",
        8 => "interface",
        9 => "module",
        10 => "property",
        13 => "enum",
        14 => "keyword",
        21 => "constant",
        22 => "struct",
        _ => "text",
    }
    .to_string()
}

fn map_hover(value: Value) -> Option<String> {
    let result = value.get("result")?;
    if result.is_null() {
        return None;
    }
    let contents = result.get("contents")?;

    if let Some(s) = contents.as_str() {
        return Some(s.to_string());
    }
    if let Some(v) = contents.get("value").and_then(|v| v.as_str()) {
        return Some(v.to_string());
    }
    if let Some(arr) = contents.as_array() {
        let parts: Vec<String> = arr
            .iter()
            .filter_map(|c| {
                c.as_str()
                    .map(|s| s.to_string())
                    .or_else(|| c.get("value").and_then(|v| v.as_str()).map(|s| s.to_string()))
            })
            .collect();
        if !parts.is_empty() {
            return Some(parts.join("\n\n"));
        }
    }
    None
}
