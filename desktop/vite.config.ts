import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

import { pdfAssetDirectories } from "./src/lib/pdf-viewer/asset-urls";

const host = process.env.TAURI_DEV_HOST;

const require = createRequire(import.meta.url);

/** The one list, imported rather than repeated: these are the same strings
 *  `getDocument` is handed as prefixes, minus the trailing slash pdf.js needs
 *  and a route does not. A second copy here is how a rename ships a viewer
 *  that 404s at the first document load with every test still green. */
const ASSET_DIRECTORIES = Object.values(pdfAssetDirectories).map((dir) =>
  dir.replace(/\/$/, ""),
);

/** Anything not listed is served as bytes. `.wasm` has to carry its own type
 *  for `instantiateStreaming`, and the `*_nowasm_fallback.js` decoders are
 *  fetched as ES modules — a module served under the wrong type is refused
 *  before it ever runs. The rest (`.bcmap`, `.pfb`, `.ttf`, and the `LICENSE*`
 *  files beside them) are read as array buffers, which no content type
 *  affects. */
const CONTENT_TYPES: Record<string, string> = {
  ".wasm": "application/wasm",
  ".js": "text/javascript",
};

/**
 * Publishes pdf.js's runtime assets beside `index.html`, in dev and in the
 * bundle: the WebAssembly decoders, the predefined CJK CMaps and the standard
 * font metrics.
 *
 * These cannot ride the `?url` import that `usePdfDocument.ts` uses for the
 * pdf.js worker, which is the whole reason this plugin exists. pdf.js builds
 * each asset's URL by concatenating a literal filename onto the matching
 * prefix, so `openjpeg.wasm` and `UniJIS-UCS2-H.bcmap` have to still be called
 * that when they land — and Rollup hashes whatever goes through the asset
 * pipeline. The directories have to arrive verbatim or not at all.
 *
 * Each one ships whole. A partial copy is not possible for the cmaps and fonts
 * — which one a document asks for is only knowable when it is opened — and it
 * was already the wrong trade for the decoders: shipping only the two
 * `openjpeg.*` files #73 was about would have left JBIG2 failing in precisely
 * the way JPX was failing, and bought a second visit here in exchange for
 * 800 KB. The three together are ~3.8 MB against a 758 MB installer.
 *
 * The source directories are resolved through the installed package rather than
 * written-down `node_modules` paths, and a missing one throws here rather than
 * at the first document load — a pdfjs-dist bump that relocates a directory
 * should fail the build and the dev server, not ship a silently empty route.
 */
function pdfjsAssets(): Plugin {
  const pdfjsRoot = path.dirname(require.resolve("pdfjs-dist/package.json"));
  const sources = new Map(
    ASSET_DIRECTORIES.map((name) => {
      const dir = path.join(pdfjsRoot, name);
      if (!fs.existsSync(dir)) {
        throw new Error(
          `pdfjs-dist has no ${name}/ directory at ${dir}. pdf.js fetches ` +
            `these at runtime; see lib/pdf-viewer/asset-urls.ts.`,
        );
      }
      return [name, dir] as const;
    }),
  );

  return {
    name: "pdfusion:pdfjs-assets",

    // Dev serves the same paths the bundle will, so a broken prefix shows up
    // in `pnpm tauri dev` rather than waiting for someone to run a full build.
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url;
        if (!url) {
          return next();
        }
        const route = ASSET_DIRECTORIES.find((dir) =>
          url.startsWith(`/${dir}/`),
        );
        if (!route) {
          return next();
        }
        // basename, because this maps a request straight onto a path: without
        // it, `/wasm/../../etc/passwd` would be served by the dev server.
        const name = path.basename(
          url.slice(`/${route}/`.length).split("?")[0],
        );
        const file = path.join(sources.get(route)!, name);
        if (!name || !fs.existsSync(file)) {
          return next();
        }
        res.setHeader(
          "Content-Type",
          CONTENT_TYPES[path.extname(name)] ?? "application/octet-stream",
        );
        fs.createReadStream(file).pipe(res);
      });
    },

    // `generateBundle` rather than `buildStart`: it never runs during dev,
    // where emitting into a bundle that is never written would throw.
    generateBundle() {
      for (const [route, dir] of sources) {
        for (const name of fs.readdirSync(dir)) {
          this.emitFile({
            type: "asset",
            // An explicit fileName is what opts this out of Rollup's hashing.
            fileName: `${route}/${name}`,
            source: fs.readFileSync(path.join(dir, name)),
          });
        }
      }
    },
  };
}

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react(), tailwindcss(), pdfjsAssets()],

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
