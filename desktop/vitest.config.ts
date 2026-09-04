import { defineConfig } from "vitest/config";
import path from "node:path";

// Kept separate from vite.config.ts: that config is an async factory wired for
// the Tauri dev server (fixed port, HMR host), none of which a test run needs.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    // Node environment on purpose — everything under test is pure logic with
    // its Tauri/sidecar collaborators injected, so there's no DOM to stand up.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
