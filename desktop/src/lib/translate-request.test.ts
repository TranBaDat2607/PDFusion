import { describe, expect, it } from "vitest";

import {
  buildTranslateBody,
  effectiveService,
  isPairSupported,
  isSourceSupported,
} from "./translate-request";
import type { OptionsResponse } from "@/hooks/useConfig";

const FILE = "D:\\Papers\\attention is all you need.pdf";

/** `/config/options` as the sidecar sends it: Argos restricted, LLMs open. */
const OPTIONS: Pick<OptionsResponse, "services"> = {
  services: [
    {
      code: "argos",
      label: "Argos Translate (offline)",
      models: ["argostranslate"],
      // auto-source alias already expanded server-side
      supported_pairs: [
        ["auto", "vi"],
        ["en", "vi"],
      ],
    },
    {
      code: "openai",
      label: "OpenAI",
      models: ["gpt-4.1"],
      supported_pairs: null,
    },
  ],
};

type ConfigSlice = Parameters<typeof effectiveService>[0];
type ProviderSlice = Parameters<typeof effectiveService>[1][number];

function config(provider: string): ConfigSlice {
  return {
    translation: { model: { provider, model: "m" } } as ConfigSlice["translation"],
  };
}

/** `GET /providers`, trimmed to what the rule reads. */
function providers(
  keys: Partial<Record<"openai" | "gemini" | "anthropic", boolean>> = {},
): ProviderSlice[] {
  return [
    { id: "openai", requires_key: true, has_key: keys.openai ?? false },
    { id: "gemini", requires_key: true, has_key: keys.gemini ?? false },
    { id: "anthropic", requires_key: true, has_key: keys.anthropic ?? false },
    { id: "argos", requires_key: false, has_key: false },
  ];
}

describe("buildTranslateBody", () => {
  it("always carries the file, page and cache flag", () => {
    expect(buildTranslateBody({ filePath: FILE, visiblePage: 3 })).toEqual({
      file_path: FILE,
      visible_page: 3,
      bypass_cache: false,
    });
  });

  it("sends the selected languages and service", () => {
    expect(
      buildTranslateBody({
        filePath: FILE,
        visiblePage: 1,
        bypassCache: true,
        sourceLang: "en",
        targetLang: "ja",
        service: "openai",
      }),
    ).toEqual({
      file_path: FILE,
      visible_page: 1,
      bypass_cache: true,
      source_lang: "en",
      target_lang: "ja",
      service: "openai",
    });
  });

  // Regression guard for issue #12: an omitted language must stay omitted so
  // the sidecar applies the configured default. Sending a placeholder here is
  // exactly the bug — a non-null sentinel that pre-empts the real default.
  it("omits unset languages rather than substituting a placeholder", () => {
    const body = buildTranslateBody({
      filePath: FILE,
      visiblePage: 1,
      sourceLang: null,
      targetLang: undefined,
    });
    expect(body).not.toHaveProperty("source_lang");
    expect(body).not.toHaveProperty("target_lang");
    expect(body).not.toHaveProperty("service");
  });

  it("sends the Pages box's ranges", () => {
    const body = buildTranslateBody({
      filePath: FILE,
      visiblePage: 1,
      pageRanges: [
        [1, 20],
        [35, 35],
      ],
    });
    expect(body.page_ranges).toEqual([
      [1, 20],
      [35, 35],
    ]);
  });

  // No selection is the whole document, said by leaving the field out.
  it("omits page_ranges for the whole document", () => {
    const body = buildTranslateBody({ filePath: FILE, visiblePage: 1, pageRanges: null });
    expect(body).not.toHaveProperty("page_ranges");
  });
});

describe("effectiveService", () => {
  it("keeps an LLM that has a key", () => {
    expect(effectiveService(config("openai"), providers({ openai: true }))).toBe("openai");
  });

  // Mirrors the sidecar's silent downgrade. The toolbar can read "OpenAI"
  // while every run is really Argos.
  it("falls back to argos when the selected LLM has no key", () => {
    expect(effectiveService(config("openai"), providers())).toBe("argos");
    expect(effectiveService(config("anthropic"), providers())).toBe("argos");
  });

  it("leaves argos alone — it never needs a key", () => {
    expect(effectiveService(config("argos"), providers())).toBe("argos");
  });

  it("runs a provider that takes no key without one", () => {
    const local = [...providers(), { id: "local", requires_key: false, has_key: false }];
    expect(effectiveService(config("local"), local)).toBe("local");
  });

  it("falls back to argos for a provider it doesn't know yet", () => {
    // `GET /providers` still loading, or a provider this build lacks.
    expect(effectiveService(config("openai"), [])).toBe("argos");
  });
});

describe("isPairSupported", () => {
  it("accepts the pair argos actually ships", () => {
    expect(isPairSupported(OPTIONS, "argos", "en", "vi")).toBe(true);
    expect(isPairSupported(OPTIONS, "argos", "auto", "vi")).toBe(true);
  });

  it("rejects targets argos has no pack for", () => {
    expect(isPairSupported(OPTIONS, "argos", "en", "ja")).toBe(false);
    expect(isPairSupported(OPTIONS, "argos", "en", "zh-cn")).toBe(false);
  });

  it("treats a null matrix as unrestricted", () => {
    expect(isPairSupported(OPTIONS, "openai", "en", "ja")).toBe(true);
    expect(isPairSupported(OPTIONS, "openai", "zh-tw", "vi")).toBe(true);
  });

  // A sidecar too old to send `supported_pairs` must not black out the whole
  // dropdown — the server still pre-flights and answers 422.
  it("treats an unknown service as unrestricted", () => {
    expect(isPairSupported(OPTIONS, "gemini", "en", "ja")).toBe(true);
  });
});

describe("isSourceSupported", () => {
  it("offers only the sources Argos can translate from", () => {
    expect(isSourceSupported(OPTIONS, "argos", "auto")).toBe(true);
    expect(isSourceSupported(OPTIONS, "argos", "en")).toBe(true);
    expect(isSourceSupported(OPTIONS, "argos", "ja")).toBe(false);
    expect(isSourceSupported(OPTIONS, "argos", "vi")).toBe(false);
  });

  it("leaves an LLM unrestricted", () => {
    expect(isSourceSupported(OPTIONS, "openai", "ja")).toBe(true);
  });

  it("treats a service it doesn't know as unrestricted", () => {
    expect(isSourceSupported(OPTIONS, "gemini", "ja")).toBe(true);
  });
});
