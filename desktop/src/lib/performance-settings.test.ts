import { describe, expect, it } from "vitest";

import {
  PAGE_LIMIT_PRESETS,
  SIZE_LIMIT_PRESETS_MB,
  formatPageLimit,
  formatSizeLimit,
  limitOptions,
} from "./performance-settings";

describe("limitOptions", () => {
  it("offers the presets when the saved value is one of them", () => {
    expect(limitOptions(PAGE_LIMIT_PRESETS, 50, formatPageLimit).map((o) => o.value)).toEqual([
      10, 25, 50, 75, 100,
    ]);
  });

  it("adds a hand-edited value in order, so the Select can show it", () => {
    expect(limitOptions(PAGE_LIMIT_PRESETS, 42, formatPageLimit)).toEqual([
      { value: 10, label: "10 pages" },
      { value: 25, label: "25 pages" },
      { value: 42, label: "42 pages" },
      { value: 50, label: "50 pages" },
      { value: 75, label: "75 pages" },
      { value: 100, label: "100 pages" },
    ]);
  });

  it("offers the presets while the config is loading", () => {
    expect(limitOptions(SIZE_LIMIT_PRESETS_MB, undefined, formatSizeLimit)).toHaveLength(
      SIZE_LIMIT_PRESETS_MB.length,
    );
  });

  it("labels values", () => {
    expect(formatPageLimit(1)).toBe("1 page");
    expect(formatSizeLimit(12.5)).toBe("12.5 MB");
  });
});

describe("presets", () => {
  // `config/models.py`: MaxPages is 1-100, MaxFileSizeMB 1-200. A preset
  // outside them would be answered with 422 on every pick.
  it("stay within the sidecar's bounds", () => {
    expect(PAGE_LIMIT_PRESETS.every((v) => v >= 1 && v <= 100)).toBe(true);
    expect(SIZE_LIMIT_PRESETS_MB.every((v) => v >= 1 && v <= 200)).toBe(true);
  });

  it("include the defaults", () => {
    expect(PAGE_LIMIT_PRESETS).toContain(50);
    expect(SIZE_LIMIT_PRESETS_MB).toContain(50);
  });
});
