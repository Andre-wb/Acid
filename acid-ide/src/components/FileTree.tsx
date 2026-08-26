import { createEffect, createSignal, For, Show } from "solid-js";
import { invoke } from "@tauri-apps/api/core";

type Entry = {
  name: string;
  path: string;
  is_dir: boolean;
  has_children: boolean;
};

function fileIcon(name: string): string {
  if (name.endsWith(".rs")) return "🦀";
  if (name === "Cargo.toml" || name === "Cargo.lock") return "📦";
  if (name.endsWith(".toml")) return "⚙️";
  if (name.endsWith(".md")) return "📝";
  return "📄";
}

function Node(props: {
  entry: Entry;
  depth: number;
  activePath: string | null;
  refreshTrigger: number;
  onOpenFile: (path: string) => void;
}) {
  const [expanded, setExpanded] = createSignal(false);
  const [children, setChildren] = createSignal<Entry[]>([]);
  const [loaded, setLoaded] = createSignal(false);

  async function fetchChildren() {
    try {
      const list = await invoke<Entry[]>("list_dir", { path: props.entry.path });
      setChildren(list);
      setLoaded(true);
    } catch (e) {
      console.error(e);
    }
  }

  async function toggle() {
    if (!props.entry.is_dir) {
      props.onOpenFile(props.entry.path);
      return;
    }
    if (!loaded()) await fetchChildren();
    setExpanded(!expanded());
  }

  // Live refresh: if this folder is already expanded and a filesystem
  // change comes in (e.g. a file created from the terminal), silently
  // re-list it so new files show up without the user re-clicking.
  createEffect((prevTrigger: number | undefined) => {
    const trigger = props.refreshTrigger;
    if (prevTrigger !== undefined && trigger !== prevTrigger && expanded()) {
      fetchChildren();
    }
    return trigger;
  });

  return (
    <div>
      <div
        class="tree-row"
        classList={{ active: props.entry.path === props.activePath }}
        style={{ "padding-left": `${props.depth * 14 + 8}px` }}
        onClick={toggle}
      >
        <span class="tree-icon">
          {props.entry.is_dir ? (expanded() ? "▾" : "▸") : fileIcon(props.entry.name)}
        </span>
        <span class="tree-name">{props.entry.name}</span>
      </div>
      <Show when={props.entry.is_dir && expanded()}>
        <For each={children()}>
          {(child) => (
            <Node
              entry={child}
              depth={props.depth + 1}
              activePath={props.activePath}
              refreshTrigger={props.refreshTrigger}
              onOpenFile={props.onOpenFile}
            />
          )}
        </For>
      </Show>
    </div>
  );
}

export default function FileTree(props: {
  root: string | null;
  activePath: string | null;
  refreshTrigger: number;
  onOpenFile: (path: string) => void;
}) {
  const [entries, setEntries] = createSignal<Entry[]>([]);

  createEffect(() => {
    const root = props.root;
    // depend on refreshTrigger too, so top-level entries refresh when
    // files are created/deleted at the project root
    void props.refreshTrigger;
    if (!root) {
      setEntries([]);
      return;
    }
    invoke<Entry[]>("list_dir", { path: root }).then(setEntries).catch(console.error);
  });

  return (
    <div class="filetree">
      <Show
        when={props.root}
        fallback={<div class="filetree-empty">Проект не открыт</div>}
      >
        <For each={entries()}>
          {(e) => (
            <Node
              entry={e}
              depth={0}
              activePath={props.activePath}
              refreshTrigger={props.refreshTrigger}
              onOpenFile={props.onOpenFile}
            />
          )}
        </For>
      </Show>
    </div>
  );
}
