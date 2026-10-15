import React from "react";
import ReactDOM from "react-dom/client";
import { error, warn } from "@tauri-apps/plugin-log";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";
import { tauriAvailable, waitForTauri } from "./lib/tauri-ready";
import { installWebviewLogging } from "./lib/webview-log";

// Before the first render, so an error in it reaches `shell.log` too. Only in
// the shell: in a plain browser tab every forward would be a failed IPC call.
// In dev the bridge can land a moment after this runs, hence the wait.
const forwardWebviewLog = () =>
  installWebviewLogging({ console, events: window }, { warn, error });
if (tauriAvailable()) {
  forwardWebviewLog();
} else {
  waitForTauri().then(forwardWebviewLog, () => undefined);
}

if (import.meta.env.DEV) {
  console.info(
    "[PDFusion] in tauri:",
    tauriAvailable(),
    "| url:",
    window.location.href,
  );
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
