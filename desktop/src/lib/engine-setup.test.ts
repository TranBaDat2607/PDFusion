import { describe, expect, it } from "vitest";

import { shouldShowSetup, type EngineStatus } from "./engine-setup";

function status(ready: boolean): EngineStatus {
  return {
    ready,
    bundled: false,
    install: { running: false, stage: null, error: null },
    groups: [
      {
        id: "babeldoc",
        label: "Layout engine",
        ready,
        present: ready ? 183 : 19,
        total: 183,
        detail: "",
      },
    ],
  };
}

describe("shouldShowSetup", () => {
  it("shows on a first run with assets missing", () => {
    expect(shouldShowSetup(status(false), false, false)).toBe(true);
  });

  it("stays out of the way once the engine is installed", () => {
    expect(shouldShowSetup(status(true), false, false)).toBe(false);
  });

  it("honours a skip", () => {
    expect(shouldShowSetup(status(false), true, false)).toBe(false);
  });

  it("overrides a skip when a translate was actually refused", () => {
    // The user asked for the one thing the assets are needed for, so a
    // previous "Not now" can't keep the screen hidden.
    expect(shouldShowSetup(status(false), true, true)).toBe(true);
  });

  it("does not gate the app when the status probe failed", () => {
    // The sidecar is known-reachable by this point (the boot screen gated on
    // it), so a null status is our bug — blocking the workspace behind a screen
    // with no data to show would only compound it.
    expect(shouldShowSetup(null, false, false)).toBe(false);
  });

  it("still shows a forced screen with no status", () => {
    expect(shouldShowSetup(null, false, true)).toBe(true);
  });
});
