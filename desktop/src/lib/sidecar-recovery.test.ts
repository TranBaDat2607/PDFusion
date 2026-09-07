import { describe, expect, it } from "vitest";

import { shouldAutoRestart, SIDECAR_RESTART_COOLDOWN_MS } from "./sidecar-recovery";

describe("shouldAutoRestart", () => {
  it("allows an auto-restart when no crash has been recorded yet", () => {
    expect(shouldAutoRestart(null, Date.now())).toBe(true);
  });

  it("refuses a second auto-restart while still inside the cooldown window", () => {
    const now = Date.now();
    expect(shouldAutoRestart(now - 1_000, now)).toBe(false);
  });

  it("allows an auto-restart once the cooldown window has fully elapsed", () => {
    const now = Date.now();
    expect(shouldAutoRestart(now - SIDECAR_RESTART_COOLDOWN_MS - 1, now)).toBe(true);
  });

  it("treats the exact cooldown boundary as still cooling down", () => {
    const now = Date.now();
    expect(shouldAutoRestart(now - SIDECAR_RESTART_COOLDOWN_MS, now)).toBe(false);
  });

  it("does not allow a restart when the recorded attempt is in the future", () => {
    const now = Date.now();
    expect(shouldAutoRestart(now + 5_000, now)).toBe(false);
  });
});
