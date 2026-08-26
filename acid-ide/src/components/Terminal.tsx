import { createEffect, onCleanup, onMount } from "solid-js";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import "@xterm/xterm/css/xterm.css";

export default function TerminalPanel(props: { cwd: string | null }) {
  let containerRef: HTMLDivElement | undefined;
  let term: XTerm | undefined;
  let fit: FitAddon | undefined;
  let unlisten: UnlistenFn | undefined;
  let resizeObserver: ResizeObserver | undefined;
  let spawned = false;

  async function spawn(cwd: string) {
    if (spawned) return;
    spawned = true;
    try {
      // Fit first so the PTY is created with the *real* terminal size
      // right away — spawning at a default size and resizing a moment
      // later is what causes the shell to redraw itself assuming the
      // wrong width (backspace/history-recall artifacts).
      fit?.fit();
      const cols = term?.cols ?? 80;
      const rows = term?.rows ?? 24;
      await invoke("pty_spawn", { cwd, cols, rows });
    } catch (e) {
      term?.writeln(`\r\n\x1b[31m[failed to start terminal: ${e}]\x1b[0m`);
    }
  }

  onMount(async () => {
    if (!containerRef) return;

    term = new XTerm({
      theme: { background: "#181818", foreground: "#d4d4d4" },
      fontSize: 13,
      fontFamily: "SFMono-Regular, Consolas, monospace",
      cursorBlink: true,
    });
    fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef);
    fit.fit();

    term.onData((data) => {
      invoke("pty_write", { data }).catch(() => {});
    });

    unlisten = await listen<string>("pty://data", (event) => {
      term?.write(event.payload);
    });

    resizeObserver = new ResizeObserver(() => {
      fit?.fit();
      if (term) {
        invoke("pty_resize", { cols: term.cols, rows: term.rows }).catch(() => {});
      }
    });
    resizeObserver.observe(containerRef);

    if (props.cwd) await spawn(props.cwd);
  });

  // if a project is opened after the terminal already mounted, spawn then
  createEffect(() => {
    if (props.cwd && !spawned) spawn(props.cwd);
  });

  onCleanup(() => {
    unlisten?.();
    resizeObserver?.disconnect();
    term?.dispose();
  });

  return <div ref={containerRef} class="terminal-panel" />;
}
