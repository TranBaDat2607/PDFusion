import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { cleanupPage, trackDocumentLoad } from "./document-lifetime";

const fixture = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "__fixtures__",
  "standard-font.pdf",
);

let pdfjs: typeof import("pdfjs-dist/legacy/build/pdf.mjs");

// ~12 MB of JavaScript to parse; kept out of the first test's own budget.
beforeAll(async () => {
  pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
}, 60_000);

/** Fresh task per call: `getDocument` takes ownership of the buffer. */
function openTask() {
  return pdfjs.getDocument({
    data: new Uint8Array(fs.readFileSync(fixture)),
    useSystemFonts: false,
    useWorkerFetch: false,
  });
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("trackDocumentLoad", { timeout: 30_000 }, () => {
  it("resolves with the document the task resolves", async () => {
    const task = openTask();
    const load = trackDocumentLoad(task);
    const doc = await load.promise;
    expect(doc.numPages).toBe(1);
    expect(doc).toBe(await task.promise);
    await doc.destroy();
  });

  it("destroys the loading task when abandoned while still in flight", async () => {
    const task = openTask();
    const load = trackDocumentLoad(task);
    load.promise.catch(() => {});
    load.abandon();
    expect(task.destroyed).toBe(true);
    await sleep(50);
  });

  // A failed load never produced a document, so nothing else owns the task.
  it("destroys the task when abandoned after the load failed", async () => {
    const task = pdfjs.getDocument({ data: new Uint8Array([1, 2, 3]) });
    const load = trackDocumentLoad(task);
    await load.promise.catch(() => {});
    load.abandon();
    expect(task.destroyed).toBe(true);
  });

  // The owner's effect re-runs while the document is on screen; abandon()
  // is then called on a load that finished long ago.
  it("leaves an arrived document usable after abandon", async () => {
    const load = trackDocumentLoad(openTask());
    const doc = await load.promise;
    load.abandon();
    await sleep(50);
    const page = await doc.getPage(1);
    expect(page.pageNumber).toBe(1);
    await doc.destroy();
  });

  it("does not destroy the task when abandoned after the document arrived", async () => {
    const task = openTask();
    const load = trackDocumentLoad(task);
    const doc = await load.promise;
    load.abandon();
    expect(task.destroyed).toBe(false);
    await doc.destroy();
  });
});

describe("cleanupPage", { timeout: 30_000 }, () => {
  const unhandled: unknown[] = [];
  const onUnhandled = (reason: unknown) => unhandled.push(reason);

  afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
    unhandled.length = 0;
  });

  it("cleans up the page proxy while the page is still released", async () => {
    const proxy = { cleanup: vi.fn() };
    const doc = { getPage: vi.fn().mockResolvedValue(proxy) };
    cleanupPage(doc as never, 3, () => true);
    await sleep(10);
    expect(doc.getPage).toHaveBeenCalledWith(3);
    expect(proxy.cleanup).toHaveBeenCalledTimes(1);
  });

  it("skips cleanup when the page was renewed before the proxy arrived", async () => {
    const proxy = { cleanup: vi.fn() };
    const doc = { getPage: vi.fn().mockResolvedValue(proxy) };
    cleanupPage(doc as never, 3, () => false);
    await sleep(10);
    expect(proxy.cleanup).not.toHaveBeenCalled();
  });

  it("asks stillReleased only after getPage resolves, not before", async () => {
    const proxy = { cleanup: vi.fn() };
    let release!: (p: typeof proxy) => void;
    const doc = {
      getPage: vi.fn(() => new Promise<typeof proxy>((r) => (release = r))),
    };
    const stillReleased = vi.fn(() => true);
    cleanupPage(doc as never, 1, stillReleased);
    await sleep(10);
    expect(stillReleased).not.toHaveBeenCalled();
    release(proxy);
    await sleep(10);
    expect(stillReleased).toHaveBeenCalledTimes(1);
    expect(proxy.cleanup).toHaveBeenCalledTimes(1);
  });

  it("does nothing with a null document", () => {
    const stillReleased = vi.fn(() => true);
    expect(() => cleanupPage(null, 1, stillReleased)).not.toThrow();
    expect(stillReleased).not.toHaveBeenCalled();
  });

  it("swallows a getPage that throws synchronously", () => {
    const doc = {
      getPage: () => {
        throw new TypeError(
          "Cannot read properties of null (reading 'sendWithPromise')",
        );
      },
    };
    expect(() => cleanupPage(doc as never, 1, () => true)).not.toThrow();
  });

  it("leaves no unhandled rejection when getPage rejects", async () => {
    process.on("unhandledRejection", onUnhandled);
    const doc = { getPage: () => Promise.reject(new Error("x")) };
    expect(() => cleanupPage(doc as never, 1, () => true)).not.toThrow();
    await sleep(50);
    expect(unhandled).toEqual([]);
  });

  it("does not throw on a real document whose loading task was destroyed", async () => {
    const task = openTask();
    const doc = await task.promise;
    await task.destroy();
    // Without this the test could pass on a document still tearing down,
    // whose getPage would reject instead of throwing.
    expect(() => doc.getPage(1)).toThrow();
    expect(() => cleanupPage(doc, 1, () => true)).not.toThrow();
  });
});
