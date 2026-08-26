import * as monaco from "monaco-editor";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

type CompletionItem = {
  label: string;
  kind: string;
  detail: string | null;
  insert_text: string;
};

type LspDiagnostic = {
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
  severity?: number;
  message: string;
};

type PublishDiagnosticsParams = {
  uri: string;
  diagnostics: LspDiagnostic[];
};

const kindMap: Record<string, monaco.languages.CompletionItemKind> = {
  method: monaco.languages.CompletionItemKind.Method,
  function: monaco.languages.CompletionItemKind.Function,
  field: monaco.languages.CompletionItemKind.Field,
  variable: monaco.languages.CompletionItemKind.Variable,
  class: monaco.languages.CompletionItemKind.Class,
  interface: monaco.languages.CompletionItemKind.Interface,
  module: monaco.languages.CompletionItemKind.Module,
  property: monaco.languages.CompletionItemKind.Property,
  enum: monaco.languages.CompletionItemKind.Enum,
  keyword: monaco.languages.CompletionItemKind.Keyword,
  constant: monaco.languages.CompletionItemKind.Constant,
  struct: monaco.languages.CompletionItemKind.Struct,
  text: monaco.languages.CompletionItemKind.Text,
};

function severityFromLsp(sev?: number): monaco.MarkerSeverity {
  switch (sev) {
    case 1:
      return monaco.MarkerSeverity.Error;
    case 2:
      return monaco.MarkerSeverity.Warning;
    case 3:
      return monaco.MarkerSeverity.Info;
    default:
      return monaco.MarkerSeverity.Hint;
  }
}

let registered = false;

/** Registers Monaco's completion/hover providers exactly once, backed by
 * live requests to rust-analyzer through the Rust backend. */
export function registerRustLanguageFeatures() {
  if (registered) return;
  registered = true;

  monaco.languages.registerCompletionItemProvider("rust", {
    triggerCharacters: [".", ":", "<", "(", " "],
    provideCompletionItems: async (model, position) => {
      let items: CompletionItem[] = [];
      try {
        items = await invoke<CompletionItem[]>("lsp_completion", {
          uri: model.uri.toString(),
          line: position.lineNumber - 1,
          character: position.column - 1,
        });
      } catch {
        return { suggestions: [] };
      }

      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };

      return {
        suggestions: items.map((it) => ({
          label: it.label,
          kind: kindMap[it.kind] ?? monaco.languages.CompletionItemKind.Text,
          detail: it.detail ?? undefined,
          insertText: it.insert_text,
          range,
        })),
      };
    },
  });

  monaco.languages.registerHoverProvider("rust", {
    provideHover: async (model, position) => {
      let text: string | null = null;
      try {
        text = await invoke<string | null>("lsp_hover", {
          uri: model.uri.toString(),
          line: position.lineNumber - 1,
          character: position.column - 1,
        });
      } catch {
        return null;
      }
      if (!text) return null;
      return { contents: [{ value: text }] };
    },
  });
}

/** Subscribes to rust-analyzer's publishDiagnostics notifications and
 * paints them onto whichever open Monaco model they belong to. Safe to
 * call once globally — Monaco keeps its own model registry keyed by
 * URI, so we don't need to track open files here. */
export function subscribeDiagnostics() {
  listen<PublishDiagnosticsParams>("rust-analyzer://diagnostics", (event) => {
    const { uri, diagnostics } = event.payload;
    const model = monaco.editor.getModel(monaco.Uri.parse(uri));
    if (!model) return;

    const markers: monaco.editor.IMarkerData[] = diagnostics.map((d) => ({
      severity: severityFromLsp(d.severity),
      message: d.message,
      startLineNumber: d.range.start.line + 1,
      startColumn: d.range.start.character + 1,
      endLineNumber: d.range.end.line + 1,
      endColumn: d.range.end.character + 1,
    }));

    monaco.editor.setModelMarkers(model, "rust-analyzer", markers);
  });
}
