# Acid — a fast, sandboxed Rust IDE (working prototype)

Tauri 2 + Rust backend, SolidJS + Monaco frontend. Open a real multi-file
Cargo project, edit with rust-analyzer-powered autocomplete/hover and
live diagnostics, run `cargo check` on demand, and use a real embedded
shell — all in one window.

## New in this version

- **Fixed terminal rendering.** Two bugs, both fixed: (1) the shell
  wasn't given a `TERM` variable, so it fell back to "dumb" line editing
  — backspace/arrow-up-to-recall-history rely on the shell sending
  cursor-movement escape codes, which it won't do without knowing the
  terminal understands them (now sets `TERM=xterm-256color`); (2) the
  PTY was created at a hardcoded 80×24 and only resized to the real
  panel size a moment later, so the shell's first prompt was drawn
  assuming the wrong width. Now the frontend measures the terminal
  first and creates the PTY at the correct size from the start.
- **Live file tree.** The backend watches the open project directory
  (via `notify`) and tells the frontend whenever anything changes on
  disk. Currently-expanded folders quietly re-list themselves — so
  `touch foo.rs` or `cargo new` typed in the embedded terminal shows up
  in the sidebar immediately, no manual refresh.

- **Diagnostics no longer depend solely on rust-analyzer.** A debounced
  background `cargo check` (500ms after you stop typing) now runs
  alongside rust-analyzer and paints the same squiggles/problems list.
  If rust-analyzer isn't installed or fails to start, you still get
  accurate compiler warnings/errors — just slightly less instant than
  LSP diagnostics would be. Both sources write markers under different
  keys (`rust-analyzer` vs `cargo-check`), so they don't fight each other.
- **Resizable panels.** Drag the thin divider between the file tree and
  the editor, or the one above the terminal/problems panel, to resize —
  same interaction as most IDEs.
- **Autosave.** Files save to disk automatically about once a second
  while they have unsaved changes (like RustRover) — no more Cmd/Ctrl+S
  required, though it still works for an instant manual save.

## If diagnostics still don't show up

Open the terminal panel and run `cargo check` by hand in your project —
if that also produces no output/errors, the file you're editing might
already be error-free, or the project might not build for an unrelated
reason (missing dependency, wrong directory, etc). If `cargo check` in
the terminal works but Acid's UI doesn't reflect it, check the devtools
console (the background check logs failures there) for the actual error
message from either `project_check` or `lsp_start`.

- **Open real projects** — native folder picker, lazy-loaded file tree
  (directories are only read when you expand them), multi-file tabs with
  a shared Monaco editor instance, Ctrl/Cmd+S to save.
- **Embedded terminal** — a real PTY (via `portable-pty`) running your
  actual shell (`$SHELL`, or `cmd.exe` on Windows), rendered with
  `xterm.js`. Not a fake console — you can run `cargo run`, `git`,
  whatever you'd normally type.
- **rust-analyzer-backed autocomplete** — the Rust backend spawns
  `rust-analyzer` and speaks LSP to it directly over stdio (hand-rolled
  JSON-RPC client in `src-tauri/src/lsp.rs`, no extra framework). You get
  real method/field/keyword completions and hover docs, not a canned
  snippet list.
- **Live diagnostics** — rust-analyzer's `publishDiagnostics`
  notifications stream straight into Monaco markers as you type, no
  save required. A separate "cargo check" button in the bottom panel
  still runs a full project-wide check when you want the ground truth.

### Requires rust-analyzer on PATH

Autocomplete/hover/live-diagnostics need the `rust-analyzer` binary
installed and reachable on `PATH` (Rustup ships it: `rustup component
add rust-analyzer`, or grab a standalone binary from the
[rust-analyzer releases page](https://github.com/rust-lang/rust-analyzer/releases)).
If it's missing, `lsp_start` will return an error (shown in the browser
console) but the rest of the IDE — file tree, tabs, terminal, `cargo
check` — still works fine without it.

## How the pieces fit together

- **`src-tauri/src/project.rs`** — lazy file-tree listing (`list_dir`)
  plus plain `read_file`/`write_file`. No VFS abstraction, just disk.
- **`src-tauri/src/lsp.rs`** — spawns `rust-analyzer`, does the
  `initialize`/`initialized` handshake, and runs a background thread that
  reads its stdout forever: responses get routed back to whichever
  command is waiting (via a `HashMap<id, mpsc::Sender>`), and
  `publishDiagnostics` notifications get forwarded straight to the
  frontend as a Tauri event (`rust-analyzer://diagnostics`).
- **`src-tauri/src/pty.rs`** — one real PTY session (`portable-pty`),
  read in a background thread and streamed to the frontend as a
  `pty://data` event; input goes back over `pty_write`.
- **`src/lsp.ts`** — registers Monaco's completion/hover providers once,
  each call proxying through `invoke("lsp_completion"/"lsp_hover", …)`;
  and a single diagnostics subscriber that paints markers onto whichever
  Monaco model matches the URI in the event (Monaco keeps its own
  URI→model registry, so no extra bookkeeping needed on the JS side).
- **`src/App.tsx`** — owns the map of open files → Monaco models, the
  tab bar, dirty-tracking, and wires the file tree / editor / terminal /
  problems panel together.

## What this still doesn't do

It doesn't run your code inside a container/microVM yet — `cargo check`
type-checks but never executes anything, and neither does rust-analyzer.
Wiring an actual execution sandbox (Docker via the `bollard` crate, as
discussed earlier) would be a separate `run_code` command following the
same "only ever accepts source text or a project path, never a shell
string" pattern used everywhere else here.

Also: single terminal session (opening a new project kills the old PTY
and starts a fresh one in the new `cwd`), and diagnostics/completions
require `rust-analyzer` to be resolvable relative to the project's
`Cargo.toml` (a normal cargo workspace works out of the box; unusual
build setups may need more rust-analyzer configuration than this
prototype passes today).

## Prerequisites

- [Rust toolchain](https://rustup.rs) (stable) — needed for both building
  Acid itself and for the `cargo check` calls it shells out to.
- Node.js 18+ and npm.
- Tauri CLI: `cargo install tauri-cli` (or use `npm run tauri`, which
  pulls in `@tauri-apps/cli` from `package.json`).
- Platform build deps for Tauri (webview2 on Windows, webkitgtk on Linux,
  Xcode CLI tools on macOS) — see the [Tauri prerequisites guide](https://v2.tauri.app/start/prerequisites/).

## Run it

```bash
npm install
npm run tauri dev
```

This starts the Vite dev server for the SolidJS frontend and launches the
Tauri window pointed at it. Edit the Rust in the editor — diagnostics
should appear within ~250ms + however long `cargo check` takes on your
machine (typically 100–500ms once warmed up, for a single-file project).

## Build a release binary

```bash
npm run tauri build
```

The repo ships with plain placeholder PNG icons (solid brand-color
squares) so `tauri dev` runs out of the box. For an actual release build
you'll want real icons, including platform-native `.icns` (macOS) and
`.ico` (Windows) — generate the full set from a single source PNG with:

```bash
npx tauri icon path/to/your-logo.png
```

This overwrites everything in `src-tauri/icons/` and updates the formats
`tauri.conf.json` needs per platform.

## Project layout

```
src/                      SolidJS frontend
  components/Editor.tsx   Monaco wrapper, emits code changes
  App.tsx                 debounce + status bar + problems panel
  styles.css
src-tauri/
  src/main.rs             registers the check_code Tauri command
  src/checker.rs          scratch-project management + cargo check + JSON parsing
  tauri.conf.json         locked-down CSP, minimal capabilities
  capabilities/default.json
```

## Security notes baked into this skeleton

- The webview has **no filesystem or shell access** — `tauri.conf.json`'s
  CSP and the `default` capability only expose Tauri's own core APIs plus
  the one custom command you see registered in `main.rs`.
- `check_code` takes a `String` of source text only. There is no command
  that accepts a path, a shell string, or arbitrary args — so there's no
  way for the frontend (or anything injected into it) to make the backend
  run something other than "cargo-check this exact text."
- `cargo check` never executes your code, only compiles it far enough to
  type-check — so a malicious `std::process::Command::new("rm")` in the
  editor doesn't run just from having diagnostics computed. (Once you add
  an actual `run_code` execution command, that one needs the
  container/gVisor sandboxing discussed separately — checking and
  executing have very different risk profiles.)
