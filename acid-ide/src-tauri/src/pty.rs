use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use std::io::{Read, Write};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter};

/// A single interactive shell session, streamed to the frontend as raw
/// bytes over a Tauri event. One terminal is enough for this prototype;
/// the state just needs to be swappable if the user opens a new project.
pub struct PtyState {
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    child: Mutex<Option<Box<dyn Child + Send + Sync>>>,
}

impl PtyState {
    pub fn new() -> Self {
        Self {
            writer: Mutex::new(None),
            master: Mutex::new(None),
            child: Mutex::new(None),
        }
    }

    pub fn spawn(&self, cwd: String, cols: u16, rows: u16, app: AppHandle) -> Result<(), String> {
        // kill any previous session before starting a new one
        self.kill();

        let pty_system = native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| e.to_string())?;

        let shell = if cfg!(windows) {
            "cmd.exe".to_string()
        } else {
            std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".to_string())
        };

        let mut cmd = CommandBuilder::new(shell);
        cmd.cwd(cwd);
        // Without a real TERM, shells fall back to "dumb" line editing:
        // backspace/arrow-up stop sending proper cursor-movement escape
        // sequences, which is exactly the "deletes the character but not
        // the pixels, previous command appears next to the current one"
        // symptom. xterm.js understands xterm-256color natively.
        cmd.env("TERM", "xterm-256color");
        cmd.env("COLORTERM", "truecolor");

        let child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
        drop(pair.slave);

        let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer = pair.master.take_writer().map_err(|e| e.to_string())?;

        *self.writer.lock().unwrap() = Some(writer);
        *self.master.lock().unwrap() = Some(pair.master);
        *self.child.lock().unwrap() = Some(child);

        // Blocking read loop on its own OS thread — streams shell output
        // to the UI the moment it's produced, no polling.
        std::thread::spawn(move || {
            let mut buf = [0u8; 8192];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        let chunk = String::from_utf8_lossy(&buf[..n]).to_string();
                        if app.emit("pty://data", chunk).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });

        Ok(())
    }

    pub fn write(&self, data: &str) -> Result<(), String> {
        let mut guard = self.writer.lock().unwrap();
        let w = guard.as_mut().ok_or("no terminal session running")?;
        w.write_all(data.as_bytes()).map_err(|e| e.to_string())?;
        w.flush().map_err(|e| e.to_string())
    }

    pub fn resize(&self, cols: u16, rows: u16) -> Result<(), String> {
        let guard = self.master.lock().unwrap();
        let master = guard.as_ref().ok_or("no terminal session running")?;
        master
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| e.to_string())
    }

    fn kill(&self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
        }
        *self.writer.lock().unwrap() = None;
        *self.master.lock().unwrap() = None;
    }
}
