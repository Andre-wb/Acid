import { onCleanup, onMount } from "solid-js";
import * as monaco from "monaco-editor";

type Props = {
  onMountEditor: (ed: monaco.editor.IStandaloneCodeEditor) => void;
};

/** Thin wrapper around a standalone Monaco editor instance. It owns no
 * content itself — App.tsx swaps `.setModel()` on it per open file, so
 * multiple files can share a single editor instance (same as VS Code). */
export default function Editor(props: Props) {
  let containerRef: HTMLDivElement | undefined;
  let editor: monaco.editor.IStandaloneCodeEditor | undefined;

  onMount(() => {
    if (!containerRef) return;

    editor = monaco.editor.create(containerRef, {
      theme: "vs-dark",
      fontSize: 14,
      fontFamily: "SFMono-Regular, Consolas, monospace",
      minimap: { enabled: false },
      automaticLayout: true,
      tabSize: 4,
      insertSpaces: true,
      scrollBeyondLastLine: false,
      renderWhitespace: "selection",
      cursorBlinking: "smooth",
      smoothScrolling: true,
    });

    props.onMountEditor(editor);
  });

  onCleanup(() => editor?.dispose());

  return <div ref={containerRef} class="editor-wrap" />;
}
