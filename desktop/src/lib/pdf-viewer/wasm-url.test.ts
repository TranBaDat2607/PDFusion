import { describe, expect, it } from "vitest";

import { pdfWasmUrl } from "./wasm-url";

describe("pdfWasmUrl", () => {
  // The one property pdf.js will throw over. `getFactoryUrlProp` rejects a
  // prefix without a trailing slash on every document load, so this assertion
  // stands between a JPX fix and a viewer that opens nothing at all.
  it("ends in a slash whatever the base looks like", () => {
    for (const base of [
      "http://localhost:1420/",
      "http://localhost:1420/index.html",
      "tauri://localhost/",
      "http://tauri.localhost/index.html",
    ]) {
      expect(pdfWasmUrl(base).endsWith("/")).toBe(true);
    }
  });

  // Dev and bundle reach the decoders over different origins; one relative
  // prefix has to cover both, so neither origin appears in the source.
  it("resolves against the dev server", () => {
    expect(pdfWasmUrl("http://localhost:1420/index.html")).toBe(
      "http://localhost:1420/wasm/",
    );
  });

  it("resolves against the bundled app's custom protocol", () => {
    expect(pdfWasmUrl("tauri://localhost/")).toBe("tauri://localhost/wasm/");
  });

  // Vite emits the decoders beside index.html, not at the server root, so a
  // base that carries a path has to keep it — an absolute "/wasm/" would 404
  // wherever the app is not served from the root.
  it("stays beside index.html when the app sits under a subpath", () => {
    expect(pdfWasmUrl("http://example.test/app/index.html")).toBe(
      "http://example.test/app/wasm/",
    );
  });

  it("returns an absolute url, since pdf.js validates it as a fetch target", () => {
    expect(() => new URL(pdfWasmUrl("tauri://localhost/"))).not.toThrow();
  });
});
