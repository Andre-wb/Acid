use serde::Serialize;
use std::time::Instant;
use tokio::process::Command;

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Error,
    Warning,
    Note,
    Help,
}

#[derive(Debug, Serialize, Clone)]
pub struct Diagnostic {
    /// Path (relative to the project root, as cargo reports it) that
    /// this diagnostic belongs to — needed now that a project can have
    /// many files open at once.
    pub file: String,
    pub severity: Severity,
    pub message: String,
    pub line_start: u32,
    pub col_start: u32,
    pub line_end: u32,
    pub col_end: u32,
}

#[derive(Debug, Serialize)]
pub struct CheckResponse {
    pub diagnostics: Vec<Diagnostic>,
    pub elapsed_ms: u128,
}

/// Runs `cargo check` against a real, already-existing project directory
/// (as opposed to the old prototype, which checked a scratch project).
/// This is the "run a full check across the whole project" action;
/// day-to-day live diagnostics come from rust-analyzer instead (see
/// lsp.rs), which is faster because it doesn't reinvoke cargo per call.
pub async fn check_project(root: String) -> Result<CheckResponse, String> {
    let started = Instant::now();

    let output = Command::new("cargo")
        .arg("check")
        .arg("--message-format=json")
        .arg("--color=never")
        .current_dir(&root)
        .output()
        .await
        .map_err(|e| format!("failed to spawn cargo: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let diagnostics = parse_cargo_json(&stdout);

    Ok(CheckResponse {
        diagnostics,
        elapsed_ms: started.elapsed().as_millis(),
    })
}

fn parse_cargo_json(stdout: &str) -> Vec<Diagnostic> {
    let mut out = Vec::new();

    for line in stdout.lines() {
        let value: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        if value.get("reason").and_then(|r| r.as_str()) != Some("compiler-message") {
            continue;
        }

        let Some(message) = value.get("message") else {
            continue;
        };

        let level = message
            .get("level")
            .and_then(|l| l.as_str())
            .unwrap_or("note");

        let severity = match level {
            "error" | "fatal" => Severity::Error,
            "warning" => Severity::Warning,
            "help" => Severity::Help,
            _ => Severity::Note,
        };

        let text = message
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or("")
            .to_string();

        let Some(spans) = message.get("spans").and_then(|s| s.as_array()) else {
            continue;
        };

        let Some(span) = spans
            .iter()
            .find(|s| s.get("is_primary").and_then(|p| p.as_bool()) == Some(true))
        else {
            continue;
        };

        let file = span
            .get("file_name")
            .and_then(|f| f.as_str())
            .unwrap_or("")
            .to_string();

        let line_start = span.get("line_start").and_then(|v| v.as_u64()).unwrap_or(1) as u32;
        let line_end = span
            .get("line_end")
            .and_then(|v| v.as_u64())
            .unwrap_or(line_start as u64) as u32;
        let col_start = span.get("column_start").and_then(|v| v.as_u64()).unwrap_or(1) as u32;
        let col_end = span
            .get("column_end")
            .and_then(|v| v.as_u64())
            .unwrap_or(col_start as u64) as u32;

        out.push(Diagnostic {
            file,
            severity,
            message: text,
            line_start,
            col_start,
            line_end,
            col_end,
        });
    }

    out
}
