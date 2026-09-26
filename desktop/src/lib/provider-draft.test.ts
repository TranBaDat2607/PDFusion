import { describe, expect, it } from "vitest";

import {
  rebaseDraft,
  selectsAnswerModel,
  addCustomModel,
  draftFrom,
  draftProblem,
  endpointNeedsKey,
  hasSavedKey,
  isValidEndpoint,
  modelRows,
  needsVerify,
  normalizeEndpoint,
  providerUpdate,
  seedEnabled,
  selectsProvider,
  statusLine,
  timeAgo,
  toggleModel,
  verifyRequest,
  type ModelCatalog,
  type ProviderDraft,
  type ProviderInfo,
  type ProviderUpdate,
} from "./provider-draft";

const OLLAMA = "http://localhost:11434/v1";

/** `GET /providers` entry for one LLM provider — an OpenAI-like default. */
function provider(overrides: Partial<ProviderInfo> = {}): ProviderInfo {
  return {
    base_url: null,
    catalog_fetched_at: null,
    catalog_fresh: false,
    default_base_url: "https://api.openai.com/v1",
    default_model: "gpt-4.1",
    description: "OpenAI",
    enabled_models: [],
    endpoint_hint: null,
    has_key: false,
    id: "openai",
    is_llm: true,
    key_state: "unverified",
    label: "OpenAI",
    last_verified_at: null,
    max_qps: null,
    max_tokens: null,
    model: "gpt-4.1",
    model_is_fixed: false,
    priority: null,
    protocol: "openai",
    requires_key: true,
    short_label: "OpenAI",
    signup_url: null,
    suggested_models: [],
    takes_endpoint: true,
    temperature: 0.3,
    ...overrides,
  };
}

function draft(overrides: Partial<ProviderDraft> = {}): ProviderDraft {
  return {
    apiKey: "",
    clearKey: false,
    baseUrl: "",
    enabled: [],
    ...overrides,
  };
}

function catalog(overrides: Partial<ModelCatalog> = {}): ModelCatalog {
  return {
    key_state: "valid",
    models: [],
    hidden: [],
    fetched_at: null,
    error: null,
    ...overrides,
  };
}

describe("draftFrom", () => {
  it("starts with a blank key, not clearing, and the saved endpoint and models", () => {
    const p = provider({ base_url: OLLAMA, enabled_models: ["gpt-4.1", "gpt-4.1-mini"] });
    expect(draftFrom(p)).toEqual({
      apiKey: "",
      clearKey: false,
      baseUrl: OLLAMA,
      enabled: ["gpt-4.1", "gpt-4.1-mini"],
    });
  });

  it("uses an empty string for a provider with no override endpoint", () => {
    expect(draftFrom(provider({ base_url: null })).baseUrl).toBe("");
  });

  it("copies enabled_models rather than aliasing it", () => {
    const enabled_models = ["gpt-4.1"];
    const p = provider({ enabled_models });
    const d = draftFrom(p);
    d.enabled.push("gpt-4.1-mini");
    expect(enabled_models).toEqual(["gpt-4.1"]);
  });
});

describe("normalizeEndpoint", () => {
  it("trims surrounding whitespace", () => {
    expect(normalizeEndpoint(`  ${OLLAMA}  `)).toBe(OLLAMA);
  });

  it("removes a single trailing slash", () => {
    expect(normalizeEndpoint(`${OLLAMA}/`)).toBe(OLLAMA);
  });

  it("removes multiple trailing slashes", () => {
    expect(normalizeEndpoint("http://localhost:11434///")).toBe("http://localhost:11434");
  });

  it("leaves a blank value blank", () => {
    expect(normalizeEndpoint("   ")).toBe("");
  });

  it("leaves an endpoint with no trailing slash unchanged", () => {
    expect(normalizeEndpoint(OLLAMA)).toBe(OLLAMA);
  });
});

describe("isValidEndpoint", () => {
  it.each(["", "   ", OLLAMA, "https://proxy.example.com", "http://localhost:11434"])(
    "accepts %j",
    (value) => {
      expect(isValidEndpoint(value)).toBe(true);
    },
  );

  it.each([
    "localhost:11434",
    "ftp://example.com",
    "http://",
    "https://",
    "not a url",
    "/just/a/path",
  ])("refuses %j", (value) => {
    expect(isValidEndpoint(value)).toBe(false);
  });
});

describe("hasSavedKey", () => {
  it("is true when has_key is set", () => {
    expect(hasSavedKey(provider({ has_key: true, key_state: "valid" }))).toBe(true);
  });

  it("is true for an unreadable key even though has_key is false", () => {
    expect(hasSavedKey(provider({ has_key: false, key_state: "unreadable" }))).toBe(true);
  });

  it("is false with no key and no unreadable state", () => {
    expect(hasSavedKey(provider({ has_key: false, key_state: "unverified" }))).toBe(false);
  });
});

describe("endpointNeedsKey", () => {
  it("is true for a changed endpoint, a saved key, no typed key, not clearing", () => {
    const p = provider({ has_key: true, base_url: null, takes_endpoint: true });
    expect(endpointNeedsKey(draft({ baseUrl: OLLAMA }), p)).toBe(true);
  });

  it("counts an unreadable saved key the same as a readable one", () => {
    const p = provider({ has_key: false, key_state: "unreadable", base_url: null });
    expect(endpointNeedsKey(draft({ baseUrl: OLLAMA }), p)).toBe(true);
  });

  it("is false when the provider doesn't take an endpoint", () => {
    const p = provider({ has_key: true, base_url: null, takes_endpoint: false });
    expect(endpointNeedsKey(draft({ baseUrl: OLLAMA }), p)).toBe(false);
  });

  it("is false when the endpoint is unchanged, even reformatted", () => {
    const p = provider({ has_key: true, base_url: OLLAMA });
    expect(endpointNeedsKey(draft({ baseUrl: ` ${OLLAMA}/ ` }), p)).toBe(false);
  });

  it("is false with no saved key", () => {
    const p = provider({ has_key: false, key_state: "unverified", base_url: null });
    expect(endpointNeedsKey(draft({ baseUrl: OLLAMA }), p)).toBe(false);
  });

  it("is false when the draft clears the key", () => {
    const p = provider({ has_key: true, base_url: null });
    expect(endpointNeedsKey(draft({ baseUrl: OLLAMA, clearKey: true }), p)).toBe(false);
  });

  it("is false when a key is typed for the new endpoint", () => {
    const p = provider({ has_key: true, base_url: null });
    expect(endpointNeedsKey(draft({ baseUrl: OLLAMA, apiKey: "sk-new" }), p)).toBe(false);
  });
});

describe("draftProblem", () => {
  it("names http:// or https:// for an invalid endpoint", () => {
    const p = provider();
    expect(draftProblem(draft({ baseUrl: "localhost:11434" }), p)).toMatch(/https?:\/\//);
  });

  it("asks to re-enter the key when the endpoint change orphans a saved key", () => {
    const p = provider({ has_key: true, base_url: null });
    expect(draftProblem(draft({ baseUrl: OLLAMA }), p)).toMatch(/api key|key again/i);
  });

  it("prefers the endpoint-format problem over the re-enter-key one", () => {
    const p = provider({ has_key: true, base_url: null });
    expect(draftProblem(draft({ baseUrl: "localhost:11434" }), p)).toMatch(/https?:\/\//);
  });

  it("accepts an untouched draft with an empty model list", () => {
    const p = provider({ has_key: true });
    expect(draftProblem(draftFrom(p), p)).toBeNull();
  });
});

describe("providerUpdate", () => {
  it("is empty for an untouched draft", () => {
    const p = provider({ has_key: true, base_url: OLLAMA, enabled_models: ["gpt-4.1"] });
    expect(providerUpdate(draftFrom(p), p)).toEqual({});
  });

  it("sends an empty api_key to clear the saved one", () => {
    const p = provider({ has_key: true });
    expect(providerUpdate(draft({ clearKey: true }), p)).toEqual({ api_key: "" });
  });

  it("sends a typed key, trimmed", () => {
    const p = provider();
    expect(providerUpdate(draft({ apiKey: "  sk-abc  " }), p)).toEqual({ api_key: "sk-abc" });
  });

  it("omits api_key when nothing was typed and the key isn't being cleared", () => {
    const p = provider({ has_key: true, enabled_models: ["gpt-4.1", "gpt-4.1-mini"] });
    const result = providerUpdate(draft({ enabled: ["gpt-4.1"] }), p);
    expect(result.api_key).toBeUndefined();
  });

  it("sends a changed, normalized endpoint", () => {
    const p = provider({ base_url: null, has_key: false });
    expect(providerUpdate(draft({ baseUrl: `${OLLAMA}/` }), p)).toEqual({ base_url: OLLAMA });
  });

  it("sends an empty base_url to return to the provider's own endpoint", () => {
    const p = provider({ base_url: OLLAMA, has_key: false });
    expect(providerUpdate(draft({ baseUrl: "" }), p)).toEqual({ base_url: "" });
  });

  it("never sends base_url for a provider that doesn't take an endpoint", () => {
    const p = provider({ base_url: null, has_key: false, takes_endpoint: false });
    expect(providerUpdate(draft({ baseUrl: OLLAMA }), p).base_url).toBeUndefined();
  });

  it("sends enabled_models when the set changed", () => {
    const p = provider({ enabled_models: ["gpt-4.1"] });
    expect(providerUpdate(draft({ enabled: ["gpt-4.1", "gpt-4.1-mini"] }), p)).toEqual({
      enabled_models: ["gpt-4.1", "gpt-4.1-mini"],
    });
  });

  it("treats a reordering of the same names as a change", () => {
    const p = provider({ enabled_models: ["gpt-4.1", "gpt-4.1-mini"] });
    expect(providerUpdate(draft({ enabled: ["gpt-4.1-mini", "gpt-4.1"] }), p)).toEqual({
      enabled_models: ["gpt-4.1-mini", "gpt-4.1"],
    });
  });

  it("omits enabled_models when the list is unchanged", () => {
    const p = provider({ enabled_models: ["gpt-4.1"] });
    expect(providerUpdate(draft({ enabled: ["gpt-4.1"] }), p).enabled_models).toBeUndefined();
  });

  it("combines a cleared key with a new endpoint and models in one body", () => {
    const p = provider({ has_key: true, base_url: null, enabled_models: ["gpt-4.1"] });
    const d = draft({ clearKey: true, baseUrl: OLLAMA, enabled: ["gpt-4.1-mini"] });
    expect(providerUpdate(d, p)).toEqual({
      api_key: "",
      base_url: OLLAMA,
      enabled_models: ["gpt-4.1-mini"],
    });
  });
});

describe("needsVerify", () => {
  it("is true for a typed key", () => {
    expect(needsVerify(draft({ apiKey: "sk-abc" }), provider())).toBe(true);
  });

  it("is true for a changed endpoint on a provider that takes one", () => {
    const p = provider({ base_url: null, takes_endpoint: true });
    expect(needsVerify(draft({ baseUrl: OLLAMA }), p)).toBe(true);
  });

  it("is false for a changed endpoint on a provider that doesn't take one", () => {
    const p = provider({ base_url: null, takes_endpoint: false });
    expect(needsVerify(draft({ baseUrl: OLLAMA }), p)).toBe(false);
  });

  it("is false for toggling models alone", () => {
    const p = provider({ enabled_models: [] });
    expect(needsVerify(draft({ enabled: ["gpt-4.1"] }), p)).toBe(false);
  });

  it("is false for clearing the key", () => {
    expect(needsVerify(draft({ clearKey: true }), provider({ has_key: true }))).toBe(false);
  });

  it("is false for clearing the key while changing the endpoint", () => {
    // Nothing is left to check with, and the sidecar takes `api_key: ""` with
    // a new `base_url`: no saved key goes to the new endpoint.
    const p = provider({ base_url: null, has_key: true, takes_endpoint: true });
    expect(needsVerify(draft({ baseUrl: OLLAMA, clearKey: true }), p)).toBe(false);
  });

  it("is false for an untouched draft", () => {
    const p = provider({ base_url: OLLAMA, has_key: true });
    expect(needsVerify(draftFrom(p), p)).toBe(false);
  });

  it("is true for a keyless provider when only the endpoint changed", () => {
    const p = provider({ requires_key: false, base_url: null, takes_endpoint: true });
    expect(needsVerify(draft({ baseUrl: OLLAMA }), p)).toBe(true);
  });

  it("is false for a keyless provider with the endpoint unchanged, even with something typed in the key field", () => {
    const p = provider({ requires_key: false, base_url: OLLAMA, takes_endpoint: true });
    expect(needsVerify(draft({ apiKey: "unused-value", baseUrl: OLLAMA }), p)).toBe(false);
  });
});

describe("verifyRequest", () => {
  it("is null when the key is being cleared", () => {
    expect(verifyRequest(draft({ clearKey: true }), provider({ has_key: true }))).toBeNull();
  });

  it("is null with no typed key and no saved key", () => {
    const p = provider({ has_key: false, key_state: "unverified" });
    expect(verifyRequest(draft(), p)).toBeNull();
  });

  it("checks an unreadable saved key even though has_key is false", () => {
    const p = provider({
      has_key: false,
      key_state: "unreadable",
      base_url: null,
      takes_endpoint: false,
    });
    expect(verifyRequest(draft(), p)).toEqual({});
  });

  it("omits api_key with no typed key, so the server uses the saved one", () => {
    const p = provider({ has_key: true, base_url: null });
    const req = verifyRequest(draft(), p);
    expect(req?.api_key).toBeUndefined();
  });

  it("sends the typed key, trimmed", () => {
    const p = provider({ has_key: false, takes_endpoint: false });
    expect(verifyRequest(draft({ apiKey: "  sk-new  " }), p)).toEqual({ api_key: "sk-new" });
  });

  it("never sends base_url for a provider that doesn't take an endpoint", () => {
    const p = provider({ has_key: false, takes_endpoint: false });
    const req = verifyRequest(draft({ apiKey: "sk-new", baseUrl: OLLAMA }), p);
    expect(req?.base_url).toBeUndefined();
  });

  it("sends the normalized draft endpoint for a provider that takes one", () => {
    const p = provider({ has_key: false, takes_endpoint: true });
    expect(verifyRequest(draft({ apiKey: "sk-new", baseUrl: `${OLLAMA}/` }), p)).toEqual({
      api_key: "sk-new",
      base_url: OLLAMA,
    });
  });

  it("sends an empty base_url for a blank endpoint on a provider that takes one", () => {
    const p = provider({ has_key: false, takes_endpoint: true, base_url: OLLAMA });
    expect(verifyRequest(draft({ apiKey: "sk-new", baseUrl: "" }), p)).toEqual({
      api_key: "sk-new",
      base_url: "",
    });
  });

  it("is never null for a keyless provider, even with nothing typed and no saved key", () => {
    const p = provider({
      requires_key: false,
      has_key: false,
      key_state: "unverified",
      takes_endpoint: true,
      base_url: null,
    });
    expect(verifyRequest(draft(), p)).toEqual({ base_url: "" });
  });

  it("omits base_url for a keyless provider that takes no endpoint of its own", () => {
    const p = provider({
      requires_key: false,
      has_key: false,
      key_state: "unverified",
      takes_endpoint: false,
    });
    expect(verifyRequest(draft(), p)).toEqual({});
  });

  it("never carries api_key for a keyless provider, even with one typed", () => {
    const p = provider({ requires_key: false, has_key: false, takes_endpoint: false });
    const req = verifyRequest(draft({ apiKey: "unused-typed-key" }), p);
    expect(req?.api_key).toBeUndefined();
  });
});

describe("toggleModel", () => {
  it("appends a model turned on", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    expect(toggleModel(d, "gpt-4.1-mini", true).enabled).toEqual(["gpt-4.1", "gpt-4.1-mini"]);
  });

  it("doesn't duplicate a model already on", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    expect(toggleModel(d, "gpt-4.1", true).enabled).toEqual(["gpt-4.1"]);
  });

  it("removes a model turned off", () => {
    const d = draft({ enabled: ["gpt-4.1", "gpt-4.1-mini"] });
    expect(toggleModel(d, "gpt-4.1", false).enabled).toEqual(["gpt-4.1-mini"]);
  });

  it("is a no-op turning off a model that isn't enabled", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    expect(toggleModel(d, "gpt-4.1-mini", false).enabled).toEqual(["gpt-4.1"]);
  });

  it("keeps the order of the other models", () => {
    const d = draft({ enabled: ["a", "b", "c"] });
    expect(toggleModel(d, "b", false).enabled).toEqual(["a", "c"]);
  });

  it("doesn't mutate the input draft", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    toggleModel(d, "gpt-4.1-mini", true);
    expect(d.enabled).toEqual(["gpt-4.1"]);
  });
});

describe("addCustomModel", () => {
  it("appends the trimmed name", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    expect(addCustomModel(d, "  my-model  ").enabled).toEqual(["gpt-4.1", "my-model"]);
  });

  it("leaves the draft unchanged for a blank name", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    expect(addCustomModel(d, "   ").enabled).toEqual(["gpt-4.1"]);
  });

  it("doesn't duplicate a name already enabled", () => {
    const d = draft({ enabled: ["gpt-4.1", "my-model"] });
    expect(addCustomModel(d, "my-model").enabled).toEqual(["gpt-4.1", "my-model"]);
  });

  it("doesn't mutate the input draft", () => {
    const d = draft({ enabled: ["gpt-4.1"] });
    addCustomModel(d, "my-model");
    expect(d.enabled).toEqual(["gpt-4.1"]);
  });
});

describe("seedEnabled", () => {
  it("leaves a non-empty draft unchanged", () => {
    const p = provider({ suggested_models: ["gpt-4.1"] });
    const d = draft({ enabled: ["gpt-4.1-mini"] });
    expect(seedEnabled(d, p, ["gpt-4.1", "gpt-4.1-mini"]).enabled).toEqual(["gpt-4.1-mini"]);
  });

  it("seeds the suggested models that were listed, in suggestion order", () => {
    const p = provider({ suggested_models: ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"] });
    const listed = ["gpt-4.1-nano", "gpt-4.1", "o3"];
    expect(seedEnabled(draft(), p, listed).enabled).toEqual(["gpt-4.1", "gpt-4.1-nano"]);
  });

  it("falls back to the first listed id when none of the suggestions were listed", () => {
    const p = provider({ suggested_models: ["gpt-4.1"] });
    expect(seedEnabled(draft(), p, ["o3", "o3-mini"]).enabled).toEqual(["o3"]);
  });

  it("leaves the draft unchanged when nothing was listed", () => {
    const p = provider({ suggested_models: ["gpt-4.1"] });
    expect(seedEnabled(draft(), p, []).enabled).toEqual([]);
  });
});

describe("modelRows", () => {
  it("lists only the enabled ids as custom rows when there is no catalog", () => {
    const d = draft({ enabled: ["a", "b"] });
    expect(modelRows(undefined, d, { search: "", showAll: false })).toEqual([
      { id: "a", enabled: true, kind: "custom" },
      { id: "b", enabled: true, kind: "custom" },
    ]);
  });

  it("orders custom rows, then catalog models, then hidden rows", () => {
    const c = catalog({
      models: [
        { id: "gpt-4o", source: "listed" },
        { id: "gpt-4o-mini", source: "suggested" },
        { id: "saved-model", source: "saved" },
      ],
      hidden: [
        { id: "hidden-1", source: "listed" },
        { id: "hidden-2", source: "listed" },
      ],
    });
    const d = draft({ enabled: ["custom-x", "gpt-4o", "saved-model", "hidden-1"] });
    expect(modelRows(c, d, { search: "", showAll: true })).toEqual([
      { id: "custom-x", enabled: true, kind: "custom" },
      { id: "saved-model", enabled: true, kind: "custom" },
      { id: "gpt-4o", enabled: true, kind: "listed" },
      { id: "gpt-4o-mini", enabled: false, kind: "suggested" },
      { id: "hidden-1", enabled: true, kind: "hidden" },
      { id: "hidden-2", enabled: false, kind: "hidden" },
    ]);
  });

  it("lists every hidden model when showAll is true", () => {
    const c = catalog({ hidden: [{ id: "hidden-1", source: "listed" }] });
    const rows = modelRows(c, draft(), { search: "", showAll: true });
    expect(rows).toEqual([{ id: "hidden-1", enabled: false, kind: "hidden" }]);
  });

  it("lists only enabled hidden models when showAll is false", () => {
    const c = catalog({
      hidden: [
        { id: "hidden-1", source: "listed" },
        { id: "hidden-2", source: "listed" },
      ],
    });
    const d = draft({ enabled: ["hidden-1"] });
    const rows = modelRows(c, d, { search: "", showAll: false });
    expect(rows).toEqual([{ id: "hidden-1", enabled: true, kind: "hidden" }]);
  });

  it("filters rows by every whitespace-separated search word, case-insensitively", () => {
    const c = catalog({
      models: [
        { id: "gpt-4o", source: "listed" },
        { id: "gpt-4o-mini", source: "listed" },
        { id: "claude-3-opus", source: "listed" },
      ],
    });
    const rows = modelRows(c, draft(), { search: "GPT mini", showAll: false });
    expect(rows.map((r) => r.id)).toEqual(["gpt-4o-mini"]);
  });

  it("keeps every row for a blank search", () => {
    const c = catalog({ models: [{ id: "gpt-4o", source: "listed" }] });
    const rows = modelRows(c, draft(), { search: "   ", showAll: false });
    expect(rows.map((r) => r.id)).toEqual(["gpt-4o"]);
  });
});

describe("timeAgo", () => {
  const now = new Date("2026-01-01T12:00:00Z").getTime();

  it("is 'just now' under a minute", () => {
    expect(timeAgo(new Date(now - 30_000).toISOString(), now)).toBe("just now");
  });

  it("floors to whole minutes", () => {
    expect(timeAgo(new Date(now - 90_000).toISOString(), now)).toBe("1 min ago");
  });

  it("reports minutes under an hour", () => {
    expect(timeAgo(new Date(now - 30 * 60_000).toISOString(), now)).toBe("30 min ago");
  });

  it("reports hours under a day", () => {
    expect(timeAgo(new Date(now - 5 * 3_600_000 - 1_800_000).toISOString(), now)).toBe(
      "5 h ago",
    );
  });

  it("switches to hours at the one-hour mark", () => {
    expect(timeAgo(new Date(now - 3_600_000).toISOString(), now)).toBe("1 h ago");
  });

  it("uses the singular for exactly one day", () => {
    expect(timeAgo(new Date(now - 24 * 3_600_000).toISOString(), now)).toBe("1 day ago");
  });

  it("uses the plural for multiple days", () => {
    expect(timeAgo(new Date(now - 3 * 24 * 3_600_000).toISOString(), now)).toBe("3 days ago");
  });

  it("floors partial days", () => {
    expect(timeAgo(new Date(now - 36 * 3_600_000).toISOString(), now)).toBe("1 day ago");
  });
});

describe("statusLine", () => {
  const now = new Date("2026-01-01T12:00:00Z").getTime();

  it("says no key is needed when the provider doesn't require one", () => {
    const p = provider({ requires_key: false, has_key: false, key_state: "unverified" });
    expect(statusLine(p, undefined, now)).toEqual({ tone: "ok", text: "No key needed" });
  });

  it("prefers 'no key needed' over an unreadable key state", () => {
    const p = provider({ requires_key: false, has_key: false, key_state: "unreadable" });
    expect(statusLine(p, undefined, now)).toEqual({ tone: "ok", text: "No key needed" });
  });

  it("surfaces a catalog error for a keyless provider instead of 'No key needed'", () => {
    const p = provider({ requires_key: false, has_key: false, key_state: "unverified" });
    const c = catalog({ error: "connection refused" });
    expect(statusLine(p, c, now)).toEqual({ tone: "error", text: "connection refused" });
  });

  it("counts listed models for a keyless provider with a catalog and no error", () => {
    const p = provider({ requires_key: false, has_key: false, key_state: "unverified" });
    const c = catalog({ models: [{ id: "llama3.2", source: "listed" }] });
    expect(statusLine(p, c, now)).toEqual({ tone: "ok", text: "1 model" });
  });

  it("flags an unreadable saved key as an error", () => {
    const p = provider({ requires_key: true, has_key: false, key_state: "unreadable" });
    const result = statusLine(p, undefined, now);
    expect(result.tone).toBe("error");
    expect(result.text).toMatch(/couldn't be read/i);
  });

  it("reports no key saved before checking the key state further", () => {
    const p = provider({ requires_key: true, has_key: false, key_state: "invalid" });
    expect(statusLine(p, undefined, now)).toEqual({ tone: "muted", text: "No API key saved" });
  });

  it("surfaces a catalog error even with a valid key state", () => {
    const p = provider({ requires_key: true, has_key: true, key_state: "valid" });
    const c = catalog({ error: "connection refused" });
    expect(statusLine(p, c, now)).toEqual({ tone: "error", text: "connection refused" });
  });

  it("prefers a catalog error over an invalid key state", () => {
    const p = provider({ requires_key: true, has_key: true, key_state: "invalid" });
    const c = catalog({ error: "rate limited" });
    expect(statusLine(p, c, now)).toEqual({ tone: "error", text: "rate limited" });
  });

  it("flags a rejected key as an error", () => {
    const p = provider({ requires_key: true, has_key: true, key_state: "invalid" });
    const result = statusLine(p, undefined, now);
    expect(result.tone).toBe("error");
    expect(result.text).toMatch(/rejected/i);
  });

  it("counts listed models for a valid key, with plural wording", () => {
    const p = provider({
      requires_key: true,
      has_key: true,
      key_state: "valid",
      last_verified_at: new Date(now - 5 * 60_000).toISOString(),
    });
    const c = catalog({
      models: [
        { id: "gpt-4o", source: "listed" },
        { id: "gpt-4o-mini", source: "listed" },
        { id: "custom-suggestion", source: "suggested" },
      ],
    });
    expect(statusLine(p, c, now)).toEqual({ tone: "ok", text: "2 models · checked 5 min ago" });
  });

  it("uses the singular for exactly one listed model", () => {
    const p = provider({
      requires_key: true,
      has_key: true,
      key_state: "valid",
      last_verified_at: new Date(now - 5 * 60_000).toISOString(),
    });
    const c = catalog({ models: [{ id: "gpt-4o", source: "listed" }] });
    expect(statusLine(p, c, now)).toEqual({ tone: "ok", text: "1 model · checked 5 min ago" });
  });

  it("says 'Verified' with no catalog to count", () => {
    const p = provider({
      requires_key: true,
      has_key: true,
      key_state: "valid",
      last_verified_at: new Date(now - 5 * 60_000).toISOString(),
    });
    expect(statusLine(p, undefined, now)).toEqual({ tone: "ok", text: "Verified · checked 5 min ago" });
  });

  it("drops the checked-when part with no last_verified_at", () => {
    const p = provider({
      requires_key: true,
      has_key: true,
      key_state: "valid",
      last_verified_at: null,
    });
    const c = catalog({ models: [{ id: "gpt-4o", source: "listed" }] });
    expect(statusLine(p, c, now)).toEqual({ tone: "ok", text: "1 model" });
  });

  it("falls back to 'Not verified'", () => {
    const p = provider({
      requires_key: true,
      has_key: true,
      key_state: "unverified",
    });
    expect(statusLine(p, undefined, now)).toEqual({ tone: "muted", text: "Not verified" });
  });
});

describe("selectsProvider", () => {
  it("switches from Argos once a key is saved", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "openai", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(true);
  });

  it("switches when Settings was opened for this provider and it had no key", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "gemini", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "openai",
        openedFor: "gemini",
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(true);
  });

  it("doesn't switch without a saved key in this update", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "openai", has_key: false }),
        update: { enabled_models: ["gpt-4.1"] } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });

  it("doesn't switch on a key-clearing update", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "openai", has_key: true }),
        update: { api_key: "" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });

  it("doesn't switch when the user saved anyway after a failed verify", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "openai", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: true,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });

  it("doesn't switch when this provider already is the translation provider", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "openai", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "openai",
        openedFor: null,
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });

  it("doesn't switch away from another LLM without being opened for this provider", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "gemini", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "openai",
        openedFor: null,
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });

  it("doesn't switch when opened for this provider but it already had a key", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "gemini", has_key: true }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "openai",
        openedFor: "gemini",
        savedAnyway: false,
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });

  it("switches onto an alias the key listed under its dated id", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "anthropic", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "claude-haiku-4-5",
        listed: ["claude-haiku-4-5-20251001"],
      }),
    ).toBe(true);
  });

  it("switches onto an alias the key listed under its tagged id", () => {
    expect(
      selectsProvider({
        provider: provider({ id: "ollama", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "llama3.2",
        listed: ["llama3.2:latest"],
      }),
    ).toBe(true);
  });

  it("doesn't switch on a shared prefix without a '-' or ':' separator", () => {
    // "gpt-4" is a prefix of "gpt-4o" but not followed by a separator, so it's
    // a different model, not an alias.
    expect(
      selectsProvider({
        provider: provider({ id: "openai", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "gpt-4",
        listed: ["gpt-4o"],
      }),
    ).toBe(false);
  });

  it("doesn't switch when only the model name extends a listed id, not the reverse", () => {
    // Only a *listed* id may extend the saved model name (an alias resolving
    // to a dated id); the saved model extending a shorter listed id doesn't count.
    expect(
      selectsProvider({
        provider: provider({ id: "anthropic", has_key: false }),
        update: { api_key: "sk-new" } as ProviderUpdate,
        translationProvider: "argos",
        openedFor: null,
        savedAnyway: false,
        model: "claude-haiku-4-5-20251001",
        listed: ["claude-haiku-4-5"],
      }),
    ).toBe(false);
  });
});

describe("selectsProvider: only onto a model the key can use", () => {
  const base = {
    provider: provider({ id: "openai", has_key: false }),
    update: { api_key: "sk-new" } as ProviderUpdate,
    translationProvider: "argos",
    openedFor: null,
    savedAnyway: false,
  };

  it("doesn't switch onto a model the key's listing doesn't have", () => {
    // A saved `enabled_models` the new key can't run would fail every
    // paragraph, while Argos would have kept working.
    expect(selectsProvider({ ...base, model: "gpt-custom", listed: ["gpt-4.1"] })).toBe(false);
  });

  it("doesn't switch when the key's check listed nothing", () => {
    expect(selectsProvider({ ...base, model: "gpt-4.1", listed: [] })).toBe(false);
  });

  it("doesn't switch translation when Settings was opened from the chat header", () => {
    expect(
      selectsProvider({
        ...base,
        translationProvider: "anthropic",
        openedFor: "openai",
        openedFrom: "answer",
        model: "gpt-4.1",
        listed: ["gpt-4.1"],
      }),
    ).toBe(false);
  });
});

describe("selectsAnswerModel", () => {
  const base = {
    provider: provider({ id: "openai", has_key: false }),
    update: { api_key: "sk-new" } as ProviderUpdate,
    openedFor: "openai",
    openedFrom: "answer" as const,
    savedAnyway: false,
    model: "gpt-4.1",
    listed: ["gpt-4.1"],
  };

  it("makes the provider answer in chat when Settings was opened from the chat header for it", () => {
    expect(selectsAnswerModel(base)).toBe(true);
  });

  it("doesn't when opened from the toolbar, or for another provider", () => {
    expect(selectsAnswerModel({ ...base, openedFrom: "translation" })).toBe(false);
    expect(selectsAnswerModel({ ...base, openedFor: "gemini" })).toBe(false);
  });

  it("doesn't on Save anyway, without a new key, for a provider that had one, or onto an unlisted model", () => {
    expect(selectsAnswerModel({ ...base, savedAnyway: true })).toBe(false);
    expect(selectsAnswerModel({ ...base, update: {} as ProviderUpdate })).toBe(false);
    expect(
      selectsAnswerModel({ ...base, provider: provider({ id: "openai", has_key: true }) }),
    ).toBe(false);
    expect(selectsAnswerModel({ ...base, listed: ["gpt-5.6-sol"] })).toBe(false);
  });

  it("makes the provider answer in chat onto an alias the key listed under its dated id", () => {
    expect(
      selectsAnswerModel({
        ...base,
        provider: provider({ id: "anthropic", has_key: false }),
        openedFor: "anthropic",
        model: "claude-haiku-4-5",
        listed: ["claude-haiku-4-5-20251001"],
      }),
    ).toBe(true);
  });
});

describe("rebaseDraft", () => {
  it("follows the provider when the draft is untouched", () => {
    // Choosing a translation model elsewhere moves the outgoing one into its
    // provider's `enabled_models`; an idle card must show that, or its next
    // Save writes the old list back.
    const before = provider({ enabled_models: ["gpt-4.1"] });
    const after = provider({ enabled_models: ["gpt-5.6-sol", "gpt-4.1"] });

    expect(rebaseDraft(draftFrom(before), before, after)).toEqual(draftFrom(after));
  });

  it("keeps a draft the user has edited", () => {
    const before = provider({ enabled_models: ["gpt-4.1"] });
    const after = provider({ enabled_models: ["gpt-5.6-sol", "gpt-4.1"] });
    const edited = draft({ apiKey: "sk-typed", enabled: ["gpt-4.1"] });

    expect(rebaseDraft(edited, before, after)).toBe(edited);
  });
});

describe("verifyRequest: the endpoint", () => {
  it("always names the endpoint to check, unchanged or not, blank for the provider's own", () => {
    // Left out, the sidecar would check the saved endpoint — the same one,
    // today. Named, a check can never fall through to a different saved
    // endpoint than the one the card shows.
    const p = provider({ has_key: true, takes_endpoint: true, base_url: null });

    expect(verifyRequest(draft(), p)).toEqual({ base_url: "" });
    expect(verifyRequest(draft({ baseUrl: OLLAMA }), provider({ has_key: true, base_url: OLLAMA }))).toEqual({
      base_url: OLLAMA,
    });
  });
});
