import { beforeEach, describe, expect, it } from "vitest";

import { getSidecar, resetSidecar, setSidecar, waitForSidecar } from "./api-client";

beforeEach(() => resetSidecar());

describe("sidecar info cache", () => {
  it("returns null before any sidecar info has been set", () => {
    expect(getSidecar()).toBeNull();
  });

  it("resolves immediately once info has already been set", async () => {
    const info = { port: 1111, token: "a" };
    setSidecar(info);
    await expect(waitForSidecar()).resolves.toEqual(info);
  });

  it("queues a caller until setSidecar is called", async () => {
    let resolved = false;
    const pending = waitForSidecar().then((info) => {
      resolved = true;
      return info;
    });

    await Promise.resolve();
    expect(resolved).toBe(false);

    const info = { port: 2222, token: "b" };
    setSidecar(info);
    await expect(pending).resolves.toEqual(info);
  });

  it("resolves every caller queued before setSidecar to the same info", async () => {
    const first = waitForSidecar();
    const second = waitForSidecar();
    const info = { port: 3333, token: "c" };
    setSidecar(info);
    await expect(first).resolves.toEqual(info);
    await expect(second).resolves.toEqual(info);
  });

  it("makes the next waitForSidecar queue again instead of returning stale info", async () => {
    const oldInfo = { port: 4444, token: "old" };
    setSidecar(oldInfo);

    resetSidecar();
    expect(getSidecar()).toBeNull();

    let resolved = false;
    const pending = waitForSidecar().then((info) => {
      resolved = true;
      return info;
    });
    await Promise.resolve();
    expect(resolved).toBe(false);

    const newInfo = { port: 5555, token: "new" };
    setSidecar(newInfo);
    await expect(pending).resolves.toEqual(newInfo);
  });
});
