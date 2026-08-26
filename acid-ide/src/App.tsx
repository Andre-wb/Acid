import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import * as monaco from "monaco-editor";
import Editor from "./components/Editor";
import FileTree from "./components/FileTree";
import TerminalPanel from "./components/Terminal";

type ProjectDiagnostic = {
  file: string;
  severity: "error" | "warning" | "note" | "help";
  message: string;
  line_start: number;
  col_start: number;
  line_end: number;
  col_end: number;
};

type CheckResponse = {
  diagnostics: ProjectDiagnostic[];
  elapsed_ms: number;
};

type OpenTab = { path: string; name: string; uri: string };

const LSP_CHANGE_DEBOUNCE_MS = 200;
// How long to wait after the user stops typing before re-running
// `cargo check` in the background. This is the fallback diagnostics
// source — it works even if rust-analyzer isn't installed or fails to
// start, so warnings/errors never silently disappear.
const CARGO_CHECK_DEBOUNCE_MS = 500;
const AUTOSAVE_INTERVAL_MS = 1000;

function severityToMonaco(sev: ProjectDiagnostic["severity"]): monaco.MarkerSeverity {
  switch (sev) {
    case "error":
      return monaco.MarkerSeverity.Error;
    case "warning":
      return monaco.MarkerSeverity.Warning;
    default:
      return monaco.MarkerSeverity.Info;
  }
}

export default function App() {
  const [root, setRoot] = createSignal<string | null>(null);
  const [tabs, setTabs] = createSignal<OpenTab[]>([]);
  const [active, setActive] = createSignal<string | null>(null);
  const [dirty, setDirty] = createSignal<Set<string>>(new Set());
  const [bottomTab, setBottomTab] = createSignal<"terminal" | "problems">("terminal");
  const [problems, setProblems] = createSignal<ProjectDiagnostic[]>([]);
  const [checking, setChecking] = createSignal(false);
  const [sidebarWidth, setSidebarWidth] = createSignal(240);
  const [bottomHeight, setBottomHeight] = createSignal(240);
  const [fsRefresh, setFsRefresh] = createSignal(0);

  let editorRef: monaco.editor.IStandaloneCodeEditor | undefined;
  const models = new Map<string, monaco.editor.ITextModel>();
  const lspChangeTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let cargoCheckTimer: ReturnType<typeof setTimeout> | undefined;
  let autosaveTimer: ReturnType<typeof setInterval> | undefined;

  function fileName(path: string) {
    return path.split(/[/\\]/).pop() ?? path;
  }

  function fullPathFor(relativeOrAbsolute: string, projectRoot: string) {
    return relativeOrAbsolute.startsWith("/")
      ? relativeOrAbsolute
      : `${projectRoot}/${relativeOrAbsolute}`;
  }

  // ---- diagnostics (background cargo check, independent of rust-analyzer) ----

  function scheduleBackgroundCheck() {
    if (cargoCheckTimer) clearTimeout(cargoCheckTimer);
    cargoCheckTimer = setTimeout(runBackgroundCheck, CARGO_CHECK_DEBOUNCE_MS);
  }

  async function runBackgroundCheck() {
    const r = root();
    if (!r) return;
    try {
      const res = await invoke<CheckResponse>("project_check", { root: r });
      setProblems(res.diagnostics);
      applyMarkersToOpenFiles(res.diagnostics, r);
    } catch (e) {
      console.error("background cargo check failed:", e);
    }
  }

  /** Paints (or clears) markers on every currently-open model, keyed by
   * file path, so files that used to have errors and no longer do get
   * their squiggles cleared too. */
  function applyMarkersToOpenFiles(diags: ProjectDiagnostic[], projectRoot: string) {
    const byPath = new Map<string, ProjectDiagnostic[]>();
    for (const d of diags) {
      const full = fullPathFor(d.file, projectRoot);
      if (!byPath.has(full)) byPath.set(full, []);
      byPath.get(full)!.push(d);
    }

    for (const [path, model] of models.entries()) {
      const list = byPath.get(path) ?? [];
      monaco.editor.setModelMarkers(
        model,
        "cargo-check",
        list.map((d) => ({
          severity: severityToMonaco(d.severity),
          message: d.message,
          startLineNumber: d.line_start,
          startColumn: d.col_start,
          endLineNumber: d.line_end,
          endColumn: d.col_end,
        }))
      );
    }
  }

  async function runCheck() {
    const r = root();
    if (!r) return;
    setChecking(true);
    setBottomTab("problems");
    if (cargoCheckTimer) clearTimeout(cargoCheckTimer);
    try {
      const res = await invoke<CheckResponse>("project_check", { root: r });
      setProblems(res.diagnostics);
      applyMarkersToOpenFiles(res.diagnostics, r);
    } catch (e) {
      console.error(e);
    } finally {
      setChecking(false);
    }
  }

  // ---- project / files ----

  async function openProject() {
    const picked = await invoke<string | null>("open_project").catch(() => null);
    if (!picked) return;

    for (const m of models.values()) m.dispose();
    models.clear();
    setTabs([]);
    setActive(null);
    setDirty(new Set());
    setProblems([]);

    setRoot(picked);
    invoke("lsp_start", { root: picked }).catch((e) => console.error("lsp_start failed:", e));
    invoke("watch_project", { root: picked }).catch((e) => console.error("watch_project failed:", e));
    scheduleBackgroundCheck();
  }

  async function openFile(path: string) {
    if (models.has(path)) {
      setActive(path);
      editorRef?.setModel(models.get(path)!);
      return;
    }

    let contents: string;
    try {
      contents = await invoke<string>("read_file", { path });
    } catch (e) {
      console.error(e);
      return;
    }

    const uri = monaco.Uri.file(path);
    const model = monaco.editor.createModel(contents, "rust", uri);
    models.set(path, model);

    setTabs([...tabs(), { path, name: fileName(path), uri: uri.toString() }]);
    setActive(path);
    editorRef?.setModel(model);

    invoke("lsp_did_open", { uri: uri.toString(), text: contents }).catch(() => {});

    model.onDidChangeContent(() => {
      markDirty(path);

      const existing = lspChangeTimers.get(path);
      if (existing) clearTimeout(existing);
      lspChangeTimers.set(
        path,
        setTimeout(() => {
          invoke("lsp_did_change", { uri: uri.toString(), text: model.getValue() }).catch(() => {});
        }, LSP_CHANGE_DEBOUNCE_MS)
      );

      scheduleBackgroundCheck();
    });
  }

  function markDirty(path: string) {
    const next = new Set(dirty());
    next.add(path);
    setDirty(next);
  }

  async function saveFile(path: string) {
    const model = models.get(path);
    if (!model) return;
    try {
      await invoke("write_file", { path, contents: model.getValue() });
      if (dirty().has(path)) {
        const next = new Set(dirty());
        next.delete(path);
        setDirty(next);
      }
    } catch (e) {
      console.error("save failed:", e);
    }
  }

  function closeTab(path: string, e: MouseEvent) {
    e.stopPropagation();
    const model = models.get(path);
    if (model) {
      const uri = model.uri.toString();
      invoke("lsp_did_close", { uri }).catch(() => {});
      model.dispose();
    }
    models.delete(path);

    const remaining = tabs().filter((t) => t.path !== path);
    setTabs(remaining);

    if (active() === path) {
      const fallback = remaining[remaining.length - 1]?.path ?? null;
      setActive(fallback);
      editorRef?.setModel(fallback ? models.get(fallback)! : null);
    }
  }

  async function jumpToProblem(d: ProjectDiagnostic) {
    const r = root();
    if (!r) return;
    await openFile(fullPathFor(d.file, r));
    editorRef?.revealLineInCenter(d.line_start);
    editorRef?.setPosition({ lineNumber: d.line_start, column: d.col_start });
    editorRef?.focus();
  }

  // ---- resizable panels ----

  function startSidebarResize(e: MouseEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth();

    function onMove(ev: MouseEvent) {
      const next = Math.min(560, Math.max(160, startWidth + (ev.clientX - startX)));
      setSidebarWidth(next);
    }
    function onUp() {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function startBottomResize(e: MouseEvent) {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = bottomHeight();

    function onMove(ev: MouseEvent) {
      const delta = startY - ev.clientY; // dragging up grows the panel
      const next = Math.min(window.innerHeight - 160, Math.max(120, startHeight + delta));
      setBottomHeight(next);
    }
    function onUp() {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  // ---- lifecycle ----

  onMount(() => {
    // Manual save still works instantly (no need to wait for the
    // autosave tick), but is no longer required — see autosave below.
    window.addEventListener("keydown", (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        const path = active();
        if (path) saveFile(path);
      }
    });

    // RustRover-style autosave: anything dirty gets flushed to disk
    // roughly once a second, no keyboard shortcut needed.
    autosaveTimer = setInterval(() => {
      for (const path of Array.from(dirty())) {
        saveFile(path);
      }
    }, AUTOSAVE_INTERVAL_MS);

    // Files created/deleted from the embedded terminal (or anywhere
    // else on disk) bump this counter, which the file tree watches to
    // silently re-list whatever directories are currently expanded.
    listen("project://fs-changed", () => setFsRefresh((n) => n + 1));
  });

  onCleanup(() => {
    if (autosaveTimer) clearInterval(autosaveTimer);
  });

  const errorCount = () => problems().filter((p) => p.severity === "error").length;

  return (
    <div class="app">
      <div class="titlebar">
        <span class="logo">ACID</span>
        <button class="open-btn" onClick={openProject}>
          Открыть проект
        </button>
        <Show when={root()}>
          <span class="filename">{root()}</span>
        </Show>
      </div>

      <div class="body">
        <div class="sidebar" style={{ width: `${sidebarWidth()}px` }}>
          <FileTree
            root={root()}
            activePath={active()}
            refreshTrigger={fsRefresh()}
            onOpenFile={openFile}
          />
        </div>
        <div class="resize-handle-v" onMouseDown={startSidebarResize} />

        <div class="main">
          <div class="tabbar">
            <For each={tabs()}>
              {(tab) => (
                <div
                  class="tab"
                  classList={{ active: tab.path === active() }}
                  onClick={() => {
                    setActive(tab.path);
                    editorRef?.setModel(models.get(tab.path)!);
                  }}
                >
                  <span>{tab.name}</span>
                  <Show when={dirty().has(tab.path)}>
                    <span class="dirty-dot" title="сохраняется…" />
                  </Show>
                  <span class="tab-close" onClick={(e) => closeTab(tab.path, e)}>
                    ×
                  </span>
                </div>
              )}
            </For>
          </div>

          <div class="editor-area">
            <Editor onMountEditor={(ed) => (editorRef = ed)} />
            <Show when={tabs().length === 0}>
              <div class="editor-placeholder">
                {root() ? "Выберите файл слева" : "Откройте проект, чтобы начать"}
              </div>
            </Show>
          </div>

          <div class="bottom-panel" style={{ height: `${bottomHeight()}px` }}>
            <div class="resize-handle-h" onMouseDown={startBottomResize} />

            <div class="bottom-tabs">
              <span
                classList={{ "bottom-tab": true, active: bottomTab() === "terminal" }}
                onClick={() => setBottomTab("terminal")}
              >
                Терминал
              </span>
              <span
                classList={{ "bottom-tab": true, active: bottomTab() === "problems" }}
                onClick={() => setBottomTab("problems")}
              >
                Проблемы {problems().length > 0 && `(${problems().length})`}
              </span>
              <button class="check-btn" disabled={!root() || checking()} onClick={runCheck}>
                {checking() ? "Проверка…" : "cargo check"}
              </button>
            </div>

            <div
              class="bottom-content"
              style={{ display: bottomTab() === "terminal" ? "block" : "none" }}
            >
              <TerminalPanel cwd={root()} />
            </div>

            <Show when={bottomTab() === "problems"}>
              <div class="problems-panel">
                <Show when={problems().length === 0}>
                  <div class="problems-empty">Ошибок и предупреждений нет</div>
                </Show>
                <For each={problems()}>
                  {(d) => (
                    <div class="problem-row" onClick={() => jumpToProblem(d)}>
                      <span class={`badge ${d.severity}`}>{d.severity}</span>
                      <span class="problem-file">{d.file}</span>
                      <span>
                        {d.message} ({d.line_start}:{d.col_start})
                      </span>
                    </div>
                  )}
                </For>
              </div>
            </Show>
          </div>
        </div>
      </div>

      <div class={`statusbar ${errorCount() > 0 ? "error" : ""}`}>
        <span>
          <span class={`dot ${checking() ? "checking" : errorCount() > 0 ? "bad" : "ok"}`} />
          {checking() && "проверка…"}
          {!checking() && errorCount() > 0 && `${errorCount()} ошибка(и)`}
          {!checking() && errorCount() === 0 && "готово"}
        </span>
        <Show when={active()}>
          <span>{active()}</span>
        </Show>
      </div>
    </div>
  );
}
