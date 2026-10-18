import { describe, expect, it } from "vitest";

import { isNearBottom } from "./chat-scroll";

// distance from the bottom = scrollHeight - scrollTop - clientHeight
describe("isNearBottom", () => {
  it("follows a reader sitting exactly at the bottom", () => {
    // 1000 - 600 - 400 = 0
    expect(isNearBottom({ scrollTop: 600, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
  });

  it("follows through sub-pixel drift", () => {
    // 1000 - 599.5 - 400 = 0.5
    expect(isNearBottom({ scrollTop: 599.5, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
    // 1000 - 598 - 400 = 2
    expect(isNearBottom({ scrollTop: 598, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
  });

  // Arrow-Up is the shortest scroll a reader makes, and the next streamed
  // delta arrives ~30 ms later: anything it counts as "still at the bottom"
  // snaps that reader back.
  it("stops following a reader who pressed Arrow-Up once", () => {
    // 1000 - 560 - 400 = 40
    expect(isNearBottom({ scrollTop: 560, clientHeight: 400, scrollHeight: 1000 })).toBe(false);
  });

  it("stops following a reader who scrolled up a wheel tick", () => {
    // 1000 - 500 - 400 = 100
    expect(isNearBottom({ scrollTop: 500, clientHeight: 400, scrollHeight: 1000 })).toBe(false);
  });

  // Not a DOM state, but the effect's: it measures against the height the
  // previous update recorded, which is 0 before the first one, and the
  // viewport can grow once the indexing bar goes.
  it("follows when the recorded height is below the viewport's", () => {
    // 300 - 0 - 400 = -100
    expect(isNearBottom({ scrollTop: 0, clientHeight: 400, scrollHeight: 300 })).toBe(true);
  });
});
