import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";
import { tauriAvailable } from "./lib/tauri-ready";

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
