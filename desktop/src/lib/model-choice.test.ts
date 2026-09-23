import { describe, expect, it } from "vitest";

import {
  chatModel,
  isCurrent,
  modelGroups,
  pickerSummary,
  selectionUpdate,
  servicesToList,
  settingsTabFor,
} from "./model-choice";
import type { ConfigResponse, OptionsResponse } from "@/hooks/useConfig";

const OLLAMA = "http://localhost:11434/v1";

const OPTIONS: Pick<OptionsResponse, "services"> = {
  services: [
    { code: "argos", label: "Argos Translate (offline)", models: ["argostranslate"] },
    { code: "openai", label: "OpenAI", models: ["gpt-4.1", "gpt-5.6-sol"] },
    { code: "gemini", label: "Google Gemini", models: ["gemini-3.8-flash"] },
    { code: "anthropic", label: "Anthropic Claude", models: ["claude-sonnet-4-6"] },
  ],
};

type Service = { has_key?: boolean; model?: string; base_url?: string | null };
type Config = Parameters<typeof pickerSummary>[0];

function config(
  preferred: ConfigResponse["translation"]["preferred_service"],
  services: Partial<Record<"openai" | "gemini" | "anthropic", Service>> = {},
): Config {
  const service = (code: "openai" | "gemini" | "anthropic") => ({
    has_key: services[code]?.has_key ?? false,
    model: services[code]?.model ?? OPTIONS.services.find((s) => s.code === code)!.models[0],
    base_url: services[code]?.base_url ?? null,
  });
  return {
    translation: { preferred_service: preferred } as Config["translation"],
    openai: service("openai"),
    gemini: service("gemini"),
    anthropic: service("anthropic"),
    argos: { has_key: false, model: "argostranslate" },
  };
}

const group = (c: Config, code: string, listed = {}) =>
  modelGroups(c, OPTIONS, listed).find((g) => g.code === code)!;

describe("modelGroups", () => {
  it("offers the suggestions, the default marked", () => {
    const openai = group(config("openai", { openai: { has_key: true } }), "openai");

    expect(openai.hasKey).toBe(true);
    expect(openai.endpoint).toBeNull();
    expect(openai.models).toEqual([
      { model: "gpt-4.1", isDefault: true },
      { model: "gpt-5.6-sol", isDefault: false },
    ]);
  });

  it("keeps a saved model the suggestions don't have, first", () => {
    const openai = group(config("openai", { openai: { model: "gpt-custom" } }), "openai");

    expect(openai.models.map((m) => m.model)).toEqual([
      "gpt-custom",
      "gpt-4.1",
      "gpt-5.6-sol",
    ]);
  });

  it("offers a custom endpoint's own models, not the provider's", () => {
    const c = config("openai", {
      openai: { has_key: true, base_url: OLLAMA, model: "llama3.2:3b" },
    });

    const openai = group(c, "openai", { openai: ["llama3.2:3b", "qwen2.5:7b"] });

    expect(openai.endpoint).toBe(OLLAMA);
    expect(openai.models).toEqual([
      { model: "llama3.2:3b", isDefault: false },
      { model: "qwen2.5:7b", isDefault: false },
    ]);
  });

  it("still offers the saved model before the endpoint answers", () => {
    const c = config("openai", {
      openai: { has_key: true, base_url: OLLAMA, model: "llama3.2:3b" },
    });

    expect(group(c, "openai").models.map((m) => m.model)).toEqual(["llama3.2:3b"]);
  });

  it("never gives Gemini an endpoint", () => {
    const c = config("gemini", { gemini: { base_url: OLLAMA } });

    expect(group(c, "gemini").endpoint).toBeNull();
  });
});

describe("selectionUpdate", () => {
  it("sends the service and model together", () => {
    const c = config("argos", { anthropic: { has_key: true } });

    expect(selectionUpdate(c, "anthropic", "claude-opus-5")).toEqual({
      preferred_service: "anthropic",
      anthropic: { model: "claude-opus-5" },
    });
  });

  it("sends only what changes", () => {
    const c = config("openai", { openai: { has_key: true } });

    expect(selectionUpdate(c, "openai", "gpt-5.6-sol")).toEqual({
      openai: { model: "gpt-5.6-sol" },
    });
    expect(selectionUpdate(c, "openai", "gpt-4.1")).toEqual({});
    expect(selectionUpdate(c, "argos", null)).toEqual({ preferred_service: "argos" });
  });
});

describe("isCurrent", () => {
  it("matches the service and its model", () => {
    const c = config("openai", { openai: { has_key: true } });

    expect(isCurrent(c, "openai", "gpt-4.1")).toBe(true);
    expect(isCurrent(c, "openai", "gpt-5.6-sol")).toBe(false);
    expect(isCurrent(c, "anthropic", "claude-sonnet-4-6")).toBe(false);
    expect(isCurrent(config("argos"), "argos", null)).toBe(true);
  });
});

describe("pickerSummary", () => {
  it("names the service and model that will run", () => {
    expect(pickerSummary(config("openai", { openai: { has_key: true } }))).toEqual({
      service: "OpenAI",
      model: "gpt-4.1",
      downgradedFrom: null,
    });
  });

  it("names Argos, and why, when the chosen LLM has no key", () => {
    expect(pickerSummary(config("anthropic"))).toEqual({
      service: "Argos",
      model: null,
      downgradedFrom: "Claude",
    });
  });

  it("has no model for Argos chosen outright", () => {
    expect(pickerSummary(config("argos"))).toEqual({
      service: "Argos",
      model: null,
      downgradedFrom: null,
    });
  });
});

describe("settingsTabFor", () => {
  it("opens on the chosen LLM, or OpenAI from Argos", () => {
    expect(settingsTabFor(config("gemini"))).toBe("gemini");
    expect(settingsTabFor(config("argos"))).toBe("openai");
  });
});

describe("servicesToList", () => {
  it("lists only keyed services pointed at another server", () => {
    const c = config("openai", {
      openai: { has_key: true, base_url: OLLAMA },
      anthropic: { has_key: false, base_url: "http://localhost:11434" },
      gemini: { has_key: true },
    });

    expect(servicesToList(c)).toEqual(["openai"]);
  });
});

describe("chatModel", () => {
  it("answers with the preferred LLM when it has a key", () => {
    const c = config("gemini", { gemini: { has_key: true }, openai: { has_key: true } });

    expect(chatModel(c)).toEqual({ service: "gemini", model: "gemini-3.8-flash" });
  });

  it("falls back in the chain's order: OpenAI, Claude, Gemini", () => {
    const c = config("argos", { gemini: { has_key: true }, anthropic: { has_key: true } });

    expect(chatModel(c)).toEqual({ service: "anthropic", model: "claude-sonnet-4-6" });
  });

  it("is null with no key anywhere", () => {
    expect(chatModel(config("openai"))).toBeNull();
  });
});
