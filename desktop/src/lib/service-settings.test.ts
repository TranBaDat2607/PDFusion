import { describe, expect, it } from "vitest";

import {
  buildConfigUpdate,
  draftProblem,
  draftsFrom,
  endpointNeedsKey,
  isValidEndpoint,
  servicesToProbe,
  validateRequestFor,
  type LlmServiceCode,
  type SavedServices,
  type ServiceDraft,
  type ServiceDrafts,
} from "./service-settings";

const OLLAMA = "http://localhost:11434/v1";

type SavedService = SavedServices["openai"];

/** `GET /config` for the three LLM services: no keys, provider endpoints. */
function saved(
  overrides: Partial<Record<LlmServiceCode, Partial<SavedService>>> = {},
): SavedServices {
  const service = (model: string, extra?: Partial<SavedService>) => ({
    has_key: false,
    model,
    base_url: null,
    ...extra,
  });
  return {
    openai: service("gpt-4.1", overrides.openai),
    gemini: service("gemini-3.8-flash", overrides.gemini),
    anthropic: service("claude-sonnet-4-6", overrides.anthropic),
  };
}

function edit(
  state: SavedServices,
  code: LlmServiceCode,
  patch: Partial<ServiceDraft>,
): ServiceDrafts {
  const drafts = draftsFrom(state);
  return { ...drafts, [code]: { ...drafts[code], ...patch } };
}

describe("buildConfigUpdate", () => {
  it("sends nothing for untouched drafts", () => {
    const state = saved({ openai: { has_key: true, base_url: OLLAMA } });
    expect(buildConfigUpdate(draftsFrom(state), state)).toEqual({});
  });

  it("sends a model that isn't one of the suggestions, trimmed", () => {
    const state = saved();
    expect(
      buildConfigUpdate(edit(state, "openai", { model: " llama3.2:3b " }), state),
    ).toEqual({ openai: { model: "llama3.2:3b" } });
  });

  it("clears a key with an empty string, which the sidecar reads as clear", () => {
    const state = saved({ gemini: { has_key: true } });
    expect(
      buildConfigUpdate(edit(state, "gemini", { clearKey: true }), state),
    ).toEqual({ gemini: { api_key: "" } });
  });

  it("sends a new endpoint without its trailing slash, with its key", () => {
    const state = saved();
    const drafts = edit(state, "openai", {
      apiKey: " ollama ",
      baseUrl: `${OLLAMA}/`,
    });
    expect(buildConfigUpdate(drafts, state)).toEqual({
      openai: { api_key: "ollama", base_url: OLLAMA },
    });
  });

  it("sends a blank endpoint to return to the provider's own", () => {
    const state = saved({ anthropic: { base_url: "http://localhost:11434" } });
    expect(
      buildConfigUpdate(edit(state, "anthropic", { baseUrl: "" }), state),
    ).toEqual({ anthropic: { base_url: "" } });
  });

  it("never sends an endpoint for Gemini", () => {
    const state = saved();
    expect(
      buildConfigUpdate(edit(state, "gemini", { baseUrl: OLLAMA }), state),
    ).toEqual({});
  });
});

describe("endpointNeedsKey", () => {
  it("needs the key again when a saved key would go to a new endpoint", () => {
    const state = saved({ openai: { has_key: true } });
    expect(
      endpointNeedsKey("openai", edit(state, "openai", { baseUrl: OLLAMA }).openai, state),
    ).toBe(true);
  });

  it("is satisfied by a typed key, or by clearing the saved one", () => {
    const state = saved({ openai: { has_key: true } });
    for (const patch of [{ apiKey: "ollama" }, { clearKey: true }]) {
      const draft = edit(state, "openai", { baseUrl: OLLAMA, ...patch }).openai;
      expect(endpointNeedsKey("openai", draft, state)).toBe(false);
    }
  });

  it("has nothing to protect without a saved key", () => {
    const state = saved();
    expect(
      endpointNeedsKey("openai", edit(state, "openai", { baseUrl: OLLAMA }).openai, state),
    ).toBe(false);
  });

  it("treats the same endpoint typed differently as no change", () => {
    const state = saved({ openai: { has_key: true, base_url: OLLAMA } });
    const draft = edit(state, "openai", { baseUrl: ` ${OLLAMA}/ ` }).openai;
    expect(endpointNeedsKey("openai", draft, state)).toBe(false);
  });
});

describe("draftProblem", () => {
  it("refuses a blank model", () => {
    const state = saved();
    expect(draftProblem("gemini", edit(state, "gemini", { model: "  " }).gemini, state)).toMatch(
      /model/,
    );
  });

  it("refuses an endpoint that isn't a web URL", () => {
    const state = saved();
    const draft = edit(state, "openai", { baseUrl: "localhost:11434" }).openai;
    expect(draftProblem("openai", draft, state)).toMatch(/http/);
  });

  it("accepts an untouched draft", () => {
    const state = saved({ openai: { has_key: true } });
    expect(draftProblem("openai", draftsFrom(state).openai, state)).toBeNull();
  });
});

describe("isValidEndpoint", () => {
  it.each(["", "  ", OLLAMA, "https://proxy.example.com"])("accepts %j", (value) => {
    expect(isValidEndpoint(value)).toBe(true);
  });

  it.each(["localhost:11434", "ftp://example.com", "http://", "/"])(
    "refuses %j",
    (value) => {
      expect(isValidEndpoint(value)).toBe(false);
    },
  );
});

describe("validateRequestFor", () => {
  it("leaves the key out so the sidecar uses the saved one", () => {
    const state = saved({ openai: { has_key: true, base_url: OLLAMA } });
    expect(validateRequestFor("openai", draftsFrom(state).openai, state)).toEqual({
      service: "openai",
      model: "gpt-4.1",
      base_url: OLLAMA,
    });
  });

  it("has nothing to check with no key, or with the key being cleared", () => {
    const state = saved({ gemini: { has_key: true } });
    expect(validateRequestFor("openai", draftsFrom(state).openai, state)).toBeNull();
    expect(
      validateRequestFor("gemini", edit(state, "gemini", { clearKey: true }).gemini, state),
    ).toBeNull();
  });
});

describe("servicesToProbe", () => {
  it("checks a changed model with the saved key", () => {
    const state = saved({ gemini: { has_key: true } });
    expect(
      servicesToProbe(edit(state, "gemini", { model: "gemini-3.7-flash" }), state),
    ).toEqual([
      { code: "gemini", request: { service: "gemini", model: "gemini-3.7-flash" } },
    ]);
  });

  it("checks a new endpoint with the key typed for it", () => {
    const state = saved({ openai: { has_key: true } });
    const drafts = edit(state, "openai", { apiKey: "ollama", baseUrl: OLLAMA });
    expect(servicesToProbe(drafts, state)).toEqual([
      {
        code: "openai",
        request: {
          service: "openai",
          api_key: "ollama",
          model: "gpt-4.1",
          base_url: OLLAMA,
        },
      },
    ]);
  });

  it("skips services that are unchanged, keyless or being cleared", () => {
    const state = saved({ anthropic: { has_key: true } });
    const drafts = {
      ...edit(state, "openai", { model: "gpt-5.6-luna" }),
      anthropic: { ...draftsFrom(state).anthropic, clearKey: true },
    };
    expect(servicesToProbe(drafts, state)).toEqual([]);
  });
});
