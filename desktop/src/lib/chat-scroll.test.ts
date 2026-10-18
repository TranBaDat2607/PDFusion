import { describe, expect, it } from "vitest";

import { FOLLOW_THRESHOLD_PX, isNearBottom } from "./chat-scroll";

// distance from the bottom = scrollHeight - scrollTop - clientHeight
describe("isNearBottom", () => {
  it("follows a reader sitting exactly at the bottom", () => {
    // 1000 - 600 - 400 = 0
    expect(isNearBottom({ scrollTop: 600, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
  });

  it("follows through sub-pixel drift", () => {
    // 1000 - 599.5 - 400 = 0.5
    expect(isNearBottom({ scrollTop: 599.5, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
  });

  it("follows at exactly the threshold and stops one pixel beyond it", () => {
    const at = 1000 - 400 - FOLLOW_THRESHOLD_PX;
    expect(isNearBottom({ scrollTop: at, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
    expect(isNearBottom({ scrollTop: at - 1, clientHeight: 400, scrollHeight: 1000 })).toBe(false);
  });

  it("stops following once the reader has scrolled up a wheel tick", () => {
    // 1000 - 500 - 400 = 100
    expect(isNearBottom({ scrollTop: 500, clientHeight: 400, scrollHeight: 1000 })).toBe(false);
  });

  it("follows when the content is shorter than the viewport", () => {
    // 300 - 0 - 400 = -100
    expect(isNearBottom({ scrollTop: 0, clientHeight: 400, scrollHeight: 300 })).toBe(true);
  });

  it("uses a threshold passed in", () => {
    const pos = { scrollTop: 500, clientHeight: 400, scrollHeight: 1000 }; // distance 100
    expect(isNearBottom(pos, 100)).toBe(true);
    expect(isNearBottom(pos, 99)).toBe(false);
  });
});
