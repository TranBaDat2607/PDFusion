import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createLatestThrottle } from "./throttle-latest";

describe("createLatestThrottle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits the first value at once", () => {
    const emit = vi.fn();
    const throttle = createLatestThrottle<string>(50, emit);

    throttle.push("a");

    expect(emit).toHaveBeenCalledTimes(1);
    expect(emit).toHaveBeenLastCalledWith("a");
  });

  it("emits pushes within the interval once, as the latest, when it ends", () => {
    const emit = vi.fn();
    const throttle = createLatestThrottle<string>(50, emit);

    throttle.push("a");
    throttle.push("b");
    throttle.push("c");
    expect(emit).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(49);
    expect(emit).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1);
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith("c");
  });

  it("drops a pending value on cancel", () => {
    const emit = vi.fn();
    const throttle = createLatestThrottle<string>(50, emit);

    throttle.push("a");
    throttle.push("b");
    throttle.cancel();
    vi.advanceTimersByTime(500);

    expect(emit).toHaveBeenCalledTimes(1);
  });

  it("emits a pending value at once on flush, and only once", () => {
    const emit = vi.fn();
    const throttle = createLatestThrottle<string>(50, emit);

    throttle.push("a");
    throttle.push("b");
    throttle.flush();
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith("b");

    vi.advanceTimersByTime(500);
    expect(emit).toHaveBeenCalledTimes(2);
  });

  it("emits a value pushed after a quiet interval at once", () => {
    const emit = vi.fn();
    const throttle = createLatestThrottle<string>(50, emit);

    throttle.push("a");
    vi.advanceTimersByTime(500);
    throttle.push("b");

    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith("b");
  });

  // A delta every 30 ms for a second is 33 pushes; at 50 ms that is at most
  // 20 emits (+1 for the leading one), whatever the alignment.
  it("bounds the emit rate under a steady stream", () => {
    const emit = vi.fn();
    const throttle = createLatestThrottle<number>(50, emit);

    for (let i = 0; i < 33; i++) {
      throttle.push(i);
      vi.advanceTimersByTime(30);
    }

    expect(emit.mock.calls.length).toBeLessThanOrEqual(21);
    expect(emit.mock.calls.length).toBeGreaterThan(10);
  });
});
