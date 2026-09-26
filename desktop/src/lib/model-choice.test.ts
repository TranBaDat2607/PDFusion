import { describe, expect, it } from "vitest";

import {
  answerModelUpdate,
  answeredBy,
  chatModel,
  followingModel,
  isCurrent,
  isCurrentAnswer,
  modelGroups,
  pickerSummary,
  selectionUpdate,
  settingsTargetFor,
  type ChoiceConfig,
  type PickerProvider,
} from "./model-choice";

const OLLAMA = "http://localhost:11434/v1";

type Overrides = Partial<PickerProvider>;

// What `GET /providers` reports, trimmed to what the picker reads. Registry
// order, which is also the order the groups come out in.
function providers(overrides: Partial<Record<string, Overrides>> = {}): PickerProvider[] {
  const base: PickerProvider[] = [
    {
      id: "openai",
      label: "OpenAI",
      short_label: "OpenAI",
      requires_key: true,
      has_key: false,
      takes_endpoint: true,
      base_url: null,
      default_model: "gpt-4.1",
      model: "gpt-4.1",
      enabled_models: [],
      model_is_fixed: false,
      is_llm: true,
      priority: 0,
    },
    {
      id: "gemini",
      label: "Google Gemini",
      short_label: "Gemini",
      requires_key: true,
      has_key: false,
      takes_endpoint: false,
      base_url: null,
      default_model: "gemini-3.8-flash",
      model: "gemini-3.8-flash",
      enabled_models: [],
      model_is_fixed: false,
      is_llm: true,
      priority: 2,
    },
    {
      id: "anthropic",
      label: "Anthropic Claude",
      short_label: "Claude",
      requires_key: true,
      has_key: false,
      takes_endpoint: true,
      base_url: null,
      default_model: "claude-sonnet-4-6",
      model: "claude-sonnet-4-6",
      enabled_models: [],
      model_is_fixed: false,
      is_llm: true,
      priority: 1,
    },
    {
      id: "argos",
      label: "Argos Translate (offline)",
      short_label: "Argos",
      requires_key: false,
      has_key: false,
      takes_endpoint: false,
      base_url: null,
      default_model: "argostranslate",
      model: "argostranslate",
      enabled_models: [],
      model_is_fixed: true,
      is_llm: false,
      priority: null,
    },
  ];
  return base.map((p) => ({ ...p, ...overrides[p.id] }));
}

function config(
  provider: string,
  model: string,
  answer: { provider: string; model: string } | null = null,
): ChoiceConfig {
  return {
    translation: { model: { provider, model } } as ChoiceConfig["translation"],
    rag: { answer_model: answer } as ChoiceConfig["rag"],
  };
}

const ARGOS = config("argos", "argostranslate");

const group = (c: ChoiceConfig, list: PickerProvider[], id: string) =>
  modelGroups(c, list).find((g) => g.id === id)!;

describe("modelGroups", () => {
  it("has a group per provider, in registry order", () => {
    expect(modelGroups(ARGOS, providers()).map((g) => g.id)).toEqual([
      "openai",
      "gemini",
      "anthropic",
      "argos",
    ]);
  });

  it("offers the enabled models, the default marked", () => {
    const list = providers({
      openai: { has_key: true, enabled_models: ["gpt-4.1", "gpt-5.6-sol"] },
    });

    const openai = group(ARGOS, list, "openai");

    expect(openai.usable).toBe(true);
    expect(openai.endpoint).toBeNull();
    expect(openai.models).toEqual([
      { model: "gpt-4.1", isDefault: true },
      { model: "gpt-5.6-sol", isDefault: false },
    ]);
  });

  it("offers the model it runs when none is enabled", () => {
    // A key saved before any model was switched on still has one to pick.
    const list = providers({ gemini: { has_key: true } });

    expect(group(ARGOS, list, "gemini").models.map((m) => m.model)).toEqual([
      "gemini-3.8-flash",
    ]);
  });

  it("keeps the translation model on offer after it was switched off", () => {
    const list = providers({
      openai: { has_key: true, enabled_models: ["gpt-5.6-sol"] },
    });

    const openai = group(config("openai", "gpt-custom"), list, "openai");

    expect(openai.models.map((m) => m.model)).toEqual(["gpt-5.6-sol", "gpt-custom"]);
  });

  it("keeps the answer model on offer too, once", () => {
    const list = providers({
      anthropic: { has_key: true, enabled_models: ["claude-sonnet-4-6"] },
    });
    const c = config("anthropic", "claude-sonnet-4-6", {
      provider: "anthropic",
      model: "claude-opus-5",
    });

    expect(group(c, list, "anthropic").models.map((m) => m.model)).toEqual([
      "claude-sonnet-4-6",
      "claude-opus-5",
    ]);
  });

  it("marks no default on a custom endpoint", () => {
    // The provider's default model is the provider's, not a local server's.
    const list = providers({
      openai: {
        has_key: true,
        base_url: OLLAMA,
        enabled_models: ["gpt-4.1", "llama3.2:3b"],
      },
    });

    const openai = group(ARGOS, list, "openai");

    expect(openai.endpoint).toBe(OLLAMA);
    expect(openai.models.every((m) => !m.isDefault)).toBe(true);
  });

  it("needs a key for a keyed provider without one", () => {
    const openai = group(ARGOS, providers(), "openai");

    expect(openai.usable).toBe(false);
    expect(openai.needsKey).toBe(true);
  });

  it("offers Argos its one fixed model, with no key needed", () => {
    const argos = group(ARGOS, providers(), "argos");

    expect(argos.usable).toBe(true);
    expect(argos.needsKey).toBe(false);
    expect(argos.fixed).toBe(true);
    expect(argos.models.map((m) => m.model)).toEqual(["argostranslate"]);
  });
});

describe("selectionUpdate", () => {
  it("sends the provider and model together", () => {
    expect(selectionUpdate(ARGOS, "anthropic", "claude-opus-5")).toEqual({
      translation_model: { provider: "anthropic", model: "claude-opus-5" },
    });
  });

  it("sends nothing for the model already chosen", () => {
    expect(selectionUpdate(config("openai", "gpt-4.1"), "openai", "gpt-4.1")).toEqual({});
    expect(selectionUpdate(ARGOS, "argos", "argostranslate")).toEqual({});
  });

  it("sends another model of the same provider", () => {
    expect(selectionUpdate(config("openai", "gpt-4.1"), "openai", "gpt-5.6-sol")).toEqual({
      translation_model: { provider: "openai", model: "gpt-5.6-sol" },
    });
  });
});

describe("isCurrent", () => {
  it("matches the provider and its model", () => {
    const c = config("openai", "gpt-4.1");

    expect(isCurrent(c, "openai", "gpt-4.1")).toBe(true);
    expect(isCurrent(c, "openai", "gpt-5.6-sol")).toBe(false);
    expect(isCurrent(c, "anthropic", "gpt-4.1")).toBe(false);
    expect(isCurrent(ARGOS, "argos", "argostranslate")).toBe(true);
  });
});

describe("pickerSummary", () => {
  it("names the provider and model that will run", () => {
    const list = providers({ openai: { has_key: true } });

    expect(pickerSummary(config("openai", "gpt-4.1"), list)).toEqual({
      service: "OpenAI",
      model: "gpt-4.1",
      downgradedFrom: null,
    });
  });

  it("names Argos, and why, when the chosen LLM has no key", () => {
    expect(pickerSummary(config("anthropic", "claude-sonnet-4-6"), providers())).toEqual({
      service: "Argos",
      model: null,
      downgradedFrom: "Claude",
    });
  });

  it("has no model for Argos chosen outright", () => {
    expect(pickerSummary(ARGOS, providers())).toEqual({
      service: "Argos",
      model: null,
      downgradedFrom: null,
    });
  });
});

describe("settingsTargetFor", () => {
  it("opens on the chosen LLM", () => {
    expect(settingsTargetFor(config("gemini", "gemini-3.8-flash"), providers())).toBe(
      "gemini",
    );
  });

  it("opens on the first provider that takes an endpoint from Argos", () => {
    // "Custom model or endpoint…" from Argos: a model of one's own needs a
    // server, and OpenAI's API is the one local servers speak.
    expect(settingsTargetFor(ARGOS, providers())).toBe("openai");
  });
});

describe("chatModel", () => {
  it("answers with the answer model first", () => {
    const list = providers({ openai: { has_key: true }, anthropic: { has_key: true } });
    const c = config("openai", "gpt-4.1", { provider: "anthropic", model: "claude-opus-5" });

    expect(chatModel(c, list)).toEqual({
      provider: "anthropic",
      label: "Claude",
      model: "claude-opus-5",
    });
  });

  it("gives way to the translation model when the answer model's provider has no key", () => {
    const list = providers({ openai: { has_key: true } });
    const c = config("openai", "gpt-5.6-sol", { provider: "gemini", model: "gemini-3.8-flash" });

    expect(chatModel(c, list)).toEqual({
      provider: "openai",
      label: "OpenAI",
      model: "gpt-5.6-sol",
    });
  });

  it("then falls back by priority, past a keyless answer and translation model", () => {
    const list = providers({ gemini: { has_key: true } });
    const c = config("anthropic", "claude-opus-5", { provider: "openai", model: "gpt-4.1" });

    expect(chatModel(c, list)?.provider).toBe("gemini");
  });

  it("answers with the translation model when its provider has a key", () => {
    const list = providers({ gemini: { has_key: true }, openai: { has_key: true } });

    expect(chatModel(config("gemini", "gemini-3.7-flash"), list)).toEqual({
      provider: "gemini",
      label: "Gemini",
      model: "gemini-3.7-flash",
    });
  });

  it("falls back by priority: OpenAI, Claude, Gemini", () => {
    const list = providers({ gemini: { has_key: true }, anthropic: { has_key: true } });

    expect(chatModel(ARGOS, list)).toEqual({
      provider: "anthropic",
      label: "Claude",
      model: "claude-sonnet-4-6",
    });
  });

  it("falls back with the model each provider runs", () => {
    const list = providers({ openai: { has_key: true, model: "gpt-5.6-sol" } });

    expect(chatModel(ARGOS, list)?.model).toBe("gpt-5.6-sol");
  });

  it("is null with no key anywhere", () => {
    expect(chatModel(config("openai", "gpt-4.1"), providers())).toBeNull();
  });

  it("answers with a keyless LLM when it is the chat answer model (#88)", () => {
    const list = providers({
      openai: { requires_key: false, has_key: false, priority: null },
    });
    const c = config("argos", "argostranslate", { provider: "openai", model: "llama3.2" });

    expect(chatModel(c, list)).toEqual({
      provider: "openai",
      label: "OpenAI",
      model: "llama3.2",
    });
  });

  it("answers with a keyless LLM when it is the translation model (#88)", () => {
    const list = providers({
      openai: { requires_key: false, has_key: false, priority: null },
    });

    expect(chatModel(config("openai", "llama3.2"), list)).toEqual({
      provider: "openai",
      label: "OpenAI",
      model: "llama3.2",
    });
  });

  it("never falls back to a keyless LLM by priority (#88)", () => {
    // Not chosen anywhere — translation stays on Argos, and no provider has a
    // key. A keyless local server may not even be running, unlike a keyed
    // provider's "any LLM with a key" fallback.
    const list = providers({
      openai: { requires_key: false, has_key: false, priority: null },
    });

    expect(chatModel(ARGOS, list)).toBeNull();
  });

  it("never answers with the offline engine even when chosen as the answer model", () => {
    const c = config("openai", "gpt-4.1", { provider: "argos", model: "argostranslate" });
    const list = providers({ openai: { has_key: true } });

    expect(chatModel(c, list)).toEqual({
      provider: "openai",
      label: "OpenAI",
      model: "gpt-4.1",
    });
  });
});

describe("followingModel", () => {
  it("answers with the translation model when its provider has a key, even over a higher-priority key", () => {
    const list = providers({ gemini: { has_key: true }, openai: { has_key: true } });

    expect(followingModel(config("gemini", "gemini-3.7-flash"), list)).toEqual({
      provider: "gemini",
      label: "Gemini",
      model: "gemini-3.7-flash",
    });
  });

  it("ignores a saved answer model — it shows what following translation would do", () => {
    const list = providers({ openai: { has_key: true }, anthropic: { has_key: true } });
    const c = config("openai", "gpt-4.1", { provider: "anthropic", model: "claude-opus-5" });

    expect(followingModel(c, list)).toEqual({
      provider: "openai",
      label: "OpenAI",
      model: "gpt-4.1",
    });
  });

  it("falls back by priority, with the model the provider runs, when translation is offline", () => {
    const list = providers({ anthropic: { has_key: true, model: "claude-opus-5" } });

    expect(followingModel(ARGOS, list)).toEqual({
      provider: "anthropic",
      label: "Claude",
      model: "claude-opus-5",
    });
  });

  it("falls back past a keyless translation provider, by priority", () => {
    const list = providers({ openai: { has_key: true }, gemini: { has_key: true } });
    const c = config("anthropic", "claude-sonnet-4-6");

    expect(followingModel(c, list)).toEqual({
      provider: "openai",
      label: "OpenAI",
      model: "gpt-4.1",
    });
  });

  it("is null with no key anywhere", () => {
    expect(followingModel(config("openai", "gpt-4.1"), providers())).toBeNull();
  });
});

describe("answerModelUpdate", () => {
  it("sends the model picked for chat", () => {
    expect(answerModelUpdate(config("openai", "gpt-4.1"), { provider: "anthropic", model: "claude-opus-5" })).toEqual({
      answer_model: { provider: "anthropic", model: "claude-opus-5" },
    });
  });

  it("sends null for Same as translation", () => {
    const c = config("openai", "gpt-4.1", { provider: "anthropic", model: "claude-opus-5" });

    expect(answerModelUpdate(c, null)).toEqual({ answer_model: null });
  });

  it("sends a model even when it is the translation model", () => {
    // Pinned: it stays when the translation model changes later.
    expect(answerModelUpdate(config("openai", "gpt-4.1"), { provider: "openai", model: "gpt-4.1" })).toEqual({
      answer_model: { provider: "openai", model: "gpt-4.1" },
    });
  });

  it("sends nothing for the choice already saved", () => {
    const c = config("openai", "gpt-4.1", { provider: "anthropic", model: "claude-opus-5" });

    expect(answerModelUpdate(c, { provider: "anthropic", model: "claude-opus-5" })).toEqual({});
    expect(answerModelUpdate(config("openai", "gpt-4.1"), null)).toEqual({});
  });
});

describe("isCurrentAnswer", () => {
  it("marks Same as translation while no answer model is saved", () => {
    const c = config("openai", "gpt-4.1");

    expect(isCurrentAnswer(c, null)).toBe(true);
    expect(isCurrentAnswer(c, { provider: "openai", model: "gpt-4.1" })).toBe(false);
  });

  it("marks the saved answer model", () => {
    const c = config("openai", "gpt-4.1", { provider: "anthropic", model: "claude-opus-5" });

    expect(isCurrentAnswer(c, { provider: "anthropic", model: "claude-opus-5" })).toBe(true);
    expect(isCurrentAnswer(c, null)).toBe(false);
  });
});

describe("answeredBy", () => {
  it("names the provider by its short label, then the model", () => {
    expect(answeredBy(providers(), "anthropic", "claude-opus-5")).toBe("Claude · claude-opus-5");
  });

  it("keeps a provider id this build doesn't know", () => {
    expect(answeredBy(providers(), "gone", "m-1")).toBe("gone · m-1");
  });

  it("is null for an answer no model wrote, or one saved before models were recorded", () => {
    expect(answeredBy(providers(), null, null)).toBeNull();
    expect(answeredBy(providers(), undefined, undefined)).toBeNull();
  });
});
