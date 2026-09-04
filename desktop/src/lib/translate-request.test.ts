import { describe, expect, it } from "vitest";

import {
  buildTranslateBody,
  effectiveService,
  isPairSupported,
} from "./translate-request";
import type { ConfigResponse, OptionsResponse } from "@/hooks/useConfig";

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

function config(
  preferred: ConfigResponse["translation"]["preferred_service"],
  keys: Partial<Record<"openai" | "gemini" | "anthropic", boolean>> = {},
): ConfigSlice {
  const service = (has_key: boolean) => ({ has_key, model: "m" });
  return {
    translation: { preferred_service: preferred } as ConfigSlice["translation"],
    openai: service(keys.openai ?? false),
    gemini: service(keys.gemini ?? false),
    anthropic: service(keys.anthropic ?? false),
    argos: service(false),
  };
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
});

describe("effectiveService", () => {
  it("keeps an LLM that has a key", () => {
    expect(effectiveService(config("openai", { openai: true }))).toBe("openai");
  });

  // Mirrors the sidecar's silent downgrade. The toolbar can read "OpenAI"
  // while every run is really Argos.
  it("falls back to argos when the selected LLM has no key", () => {
    expect(effectiveService(config("openai"))).toBe("argos");
    expect(effectiveService(config("anthropic"))).toBe("argos");
  });

  it("leaves argos alone — it never needs a key", () => {
    expect(effectiveService(config("argos"))).toBe("argos");
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
