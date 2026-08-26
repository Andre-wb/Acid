import { render } from "solid-js/web";
import App from "./App";
import { registerRustLanguageFeatures, subscribeDiagnostics } from "./lsp";

// Global, one-time setup: Monaco completion/hover providers backed by
// rust-analyzer, and the listener that paints its diagnostics as they
// stream in.
registerRustLanguageFeatures();
subscribeDiagnostics();

const root = document.getElementById("root");
render(() => <App />, root!);
