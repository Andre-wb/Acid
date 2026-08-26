use serde::Serialize;
use std::fs;
use std::path::Path;

/// Directories we never want to show or descend into — build artifacts,
/// VCS internals, editor state. Keeps the tree fast even on huge crates.
fn should_skip(name: &str) -> bool {
    matches!(
        name,
        "target" | ".git" | "node_modules" | ".idea" | ".vscode" | "dist"
    )
}

#[derive(Debug, Serialize, Clone)]
pub struct Entry {
    pub name: String,
    pub path: String,
    pub is_dir: bool,
    /// Lets the frontend show an expand arrow without eagerly reading
    /// the whole subtree (we list lazily, one directory at a time).
    pub has_children: bool,
}

/// Lists the immediate children of a directory. Lazy by design: the
/// frontend calls this again each time the user expands a folder,
/// instead of us walking the entire project up front.
pub fn list_dir(path: &str) -> Result<Vec<Entry>, String> {
    let dir = Path::new(path);
    let read = fs::read_dir(dir).map_err(|e| format!("failed to read {path}: {e}"))?;

    let mut entries = Vec::new();
    for item in read {
        let item = item.map_err(|e| e.to_string())?;
        let name = item.file_name().to_string_lossy().to_string();
        if should_skip(&name) {
            continue;
        }

        let p = item.path();
        let is_dir = p.is_dir();
        let has_children = if is_dir {
            fs::read_dir(&p)
                .map(|mut r| r.next().is_some())
                .unwrap_or(false)
        } else {
            false
        };

        entries.push(Entry {
            name,
            path: p.to_string_lossy().to_string(),
            is_dir,
            has_children,
        });
    }

    // directories first, then alphabetical, case-insensitive
    entries.sort_by(|a, b| {
        b.is_dir
            .cmp(&a.is_dir)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });

    Ok(entries)
}

pub fn read_file(path: &str) -> Result<String, String> {
    fs::read_to_string(path).map_err(|e| format!("failed to read {path}: {e}"))
}

pub fn write_file(path: &str, contents: &str) -> Result<(), String> {
    fs::write(path, contents).map_err(|e| format!("failed to write {path}: {e}"))
}
