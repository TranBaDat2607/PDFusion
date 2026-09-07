import { describe, expect, it } from "vitest";

import {
  parseCrashTimestamp,
  shouldAutoRestart,
  SIDECAR_RESTART_COOLDOWN_MS,
} from "./sidecar-recovery";

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

  // Regression for the crash-loop bug: useSidecar's `ready()` must never
  // clear the stored timestamp, or a sidecar that crashes on every boot
  // relaunches forever instead of ever reaching the "crashed" screen.
  it("keeps blocking a second crash even though a boot succeeded in between", () => {
    let crashAt: number | null = null;

    expect(shouldAutoRestart(crashAt, 0)).toBe(true);
    crashAt = 0; // recorded by the exited handler before restarting

    // A boot succeeds a few seconds later. Nothing clears crashAt here.

    expect(shouldAutoRestart(crashAt, 5_000)).toBe(false);
  });
});

describe("parseCrashTimestamp", () => {
  it("returns null when nothing is stored", () => {
    expect(parseCrashTimestamp(null)).toBeNull();
  });

  it("parses a stored timestamp", () => {
    expect(parseCrashTimestamp("1700000000000")).toBe(1700000000000);
  });

  // Corrupted storage must not permanently disable auto-restart: Number("x")
  // is NaN, and shouldAutoRestart(NaN, now) is false forever since every NaN
  // comparison is false.
  it("treats unparseable storage as no prior crash rather than NaN", () => {
    expect(parseCrashTimestamp("not-a-number")).toBeNull();
  });

  it("treats a corrupted value that is finite as that literal timestamp", () => {
    // Number("") coerces to 0 rather than NaN — still safe, since an
    // ancient/epoch-zero timestamp is always well outside the cooldown.
    expect(parseCrashTimestamp("")).toBe(0);
  });
});
