import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;

const require = createRequire(import.meta.url);

/** Must match `lib/pdf-viewer/wasm-url.ts`'s `WASM_DIRECTORY`. */
const WASM_ROUTE = "/wasm/";

/**
 * Publishes pdf.js's WebAssembly decoders at `/wasm/`, in dev and in the bundle.
 *
 * These cannot ride the `?url` import that `usePdfDocument.ts` uses for the
 * pdf.js worker, which is the whole reason this plugin exists. pdf.js builds
 * each decoder's URL by concatenating a literal filename onto the `wasmUrl`
 * prefix, so `openjpeg.wasm` has to still be called `openjpeg.wasm` when it
 * lands — and Rollup hashes whatever goes through the asset pipeline. The
 * directory has to arrive verbatim or not at all.
 *
 * The whole folder ships, not just the two `openjpeg.*` files #73 was about.
 * pdf.js resolves JBIG2 and ICC off the same prefix by the same mechanism, so
 * a partial copy would leave those failing in precisely the way JPX was
 * failing, and buy a second visit here in exchange for 800 KB.
 *
 * The source directory is resolved through the installed package rather than a
 * written-down `node_modules` path, so a pdfjs-dist bump that relocates the
 * folder fails the build here instead of shipping a silently empty `/wasm/`.
 */
function pdfjsWasm(): Plugin {
  const wasmDir = path.join(
    path.dirname(require.resolve("pdfjs-dist/package.json")),
    "wasm",
  );

  return {
    name: "pdfusion:pdfjs-wasm",

    // Dev serves the same paths the bundle will, so a broken prefix shows up
    // in `pnpm tauri dev` rather than waiting for someone to run a full build.
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith(WASM_ROUTE)) {
          return next();
        }
        // basename, because this maps a request straight onto a path: without
        // it, `/wasm/../../etc/passwd` would be served by the dev server.
        const name = path.basename(
          req.url.slice(WASM_ROUTE.length).split("?")[0],
        );
        const file = path.join(wasmDir, name);
        if (!name || !fs.existsSync(file)) {
          return next();
        }
        // The fallback decoders are fetched as ES modules, and a module served
        // under the wrong type is refused before it ever runs.
        res.setHeader(
          "Content-Type",
          name.endsWith(".wasm") ? "application/wasm" : "text/javascript",
        );
        fs.createReadStream(file).pipe(res);
      });
    },

    // `generateBundle` rather than `buildStart`: it never runs during dev,
    // where emitting into a bundle that is never written would throw.
    generateBundle() {
      for (const name of fs.readdirSync(wasmDir)) {
        this.emitFile({
          type: "asset",
          // An explicit fileName is what opts this out of Rollup's hashing.
          fileName: `wasm/${name}`,
          source: fs.readFileSync(path.join(wasmDir, name)),
        });
      }
    },
  };
}

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react(), tailwindcss(), pdfjsWasm()],

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
}));
