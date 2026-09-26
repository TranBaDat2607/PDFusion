/**
 * One provider card's draft on Settings → Models, and what saving it takes (#86).
 *
 * Each card saves on its own, with `PUT /providers/{id}`, after checking the
 * key with `POST /providers/{id}/verify` — which lists the key's models and
 * never generates text (#84). The sheet used to have a tab per provider and
 * one Save for them all, with the rules in `service-settings.ts`; these are
 * those rules, per provider and read from `GET /providers` rather than from a
 * list of services kept here.
 *
 * Pure, so what is sent, what is checked first and when the sidecar would
 * refuse a save all run under vitest's node environment.
 */

import type { components } from "@/lib/api-types";
import { OFFLINE_ENGINE } from "@/lib/translate-request";

export type ProviderInfo = components["schemas"]["ProviderInfo"];
export type ProviderUpdate = components["schemas"]["ProviderUpdateRequest"];
export type VerifyRequest = components["schemas"]["VerifyRequest"];
export type ModelCatalog = components["schemas"]["ModelCatalogResponse"];

export interface ProviderDraft {
  /** A key typed to replace the saved one. Empty keeps the saved key. */
  apiKey: string;
  /** Clear was clicked: saving removes the saved key. */
  clearKey: boolean;
  /** Blank means the provider's own endpoint. */
  baseUrl: string;
  /** What the pickers offer, in order: `enabled_models`. */
  enabled: string[];
}

export function draftFrom(provider: ProviderInfo): ProviderDraft {
  return {
    apiKey: "",
    clearKey: false,
    baseUrl: provider.base_url ?? "",
    enabled: [...provider.enabled_models],
  };
}

/**
 * The draft once its provider has changed on the sidecar (another card's
 * save, a model chosen in a picker): the new provider's own when the user
 * hasn't touched this one, so an idle card never writes an old list back;
 * otherwise the user's edits, as they are.
 */
export function rebaseDraft(
  draft: ProviderDraft,
  baseline: ProviderInfo,
  next: ProviderInfo,
): ProviderDraft {
  const untouched = Object.keys(providerUpdate(draft, baseline)).length === 0;
  return untouched ? draftFrom(next) : draft;
}

/** An endpoint the way the sidecar stores it: trimmed, with no trailing
 *  slash, so one server typed two ways compares equal. */
export function normalizeEndpoint(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

/** Blank, or an http(s) URL with a host: what the sidecar accepts. */
export function isValidEndpoint(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return true;
  try {
    const url = new URL(trimmed);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname !== "";
  } catch {
    return false;
  }
}

/** Whether a key is saved. One the keystore couldn't decrypt counts: the
 *  sidecar keeps it, and it decrypts again once the keystore is back — so
 *  it is guarded like any other (`routes/providers.py:endpoint_change_refusal`). */
export function hasSavedKey(provider: ProviderInfo): boolean {
  return provider.has_key || provider.key_state === "unreadable";
}

const typedKey = (draft: ProviderDraft) => (draft.clearKey ? "" : draft.apiKey.trim());

function endpointChanged(draft: ProviderDraft, provider: ProviderInfo): boolean {
  return (
    provider.takes_endpoint &&
    normalizeEndpoint(draft.baseUrl) !== (provider.base_url ?? "")
  );
}

/**
 * Whether saving would change the endpoint of a provider whose saved key is
 * not being replaced. The sidecar refuses that with a 422: a saved key is only
 * ever sent to the endpoint it was saved for, so it has to be typed again.
 */
export function endpointNeedsKey(draft: ProviderDraft, provider: ProviderInfo): boolean {
  return (
    endpointChanged(draft, provider) &&
    hasSavedKey(provider) &&
    !draft.clearKey &&
    !draft.apiKey.trim()
  );
}

/** Why this draft can't be saved as it stands, or null. */
export function draftProblem(draft: ProviderDraft, provider: ProviderInfo): string | null {
  if (!isValidEndpoint(draft.baseUrl)) {
    return "The base URL must be an http:// or https:// URL.";
  }
  if (endpointNeedsKey(draft, provider)) {
    return "Enter the API key again for the new endpoint. A saved key is only sent to the endpoint it was saved for.";
  }
  return null;
}

const sameList = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((name, i) => name === b[i]);

/** The `PUT /providers/{id}` body: only what changed. */
export function providerUpdate(draft: ProviderDraft, provider: ProviderInfo): ProviderUpdate {
  const update: ProviderUpdate = {};
  // `""` clears a key; leaving it out keeps the saved one.
  if (draft.clearKey) update.api_key = "";
  else if (draft.apiKey.trim()) update.api_key = draft.apiKey.trim();
  if (endpointChanged(draft, provider)) update.base_url = normalizeEndpoint(draft.baseUrl);
  if (!sameList(draft.enabled, provider.enabled_models)) {
    update.enabled_models = [...draft.enabled];
  }
  return update;
}

/** Whether saving checks with the provider first: a new key, or a key going
 *  to a new endpoint. Models are picked from the provider's own list, so
 *  switching them needs no check; and a cleared key leaves nothing to check
 *  with, nor any key to send to a new endpoint. */
export function needsVerify(draft: ProviderDraft, provider: ProviderInfo): boolean {
  // A keyless server has no key to type; only a new endpoint is news.
  if (!provider.requires_key) return endpointChanged(draft, provider);
  if (draft.clearKey) return false;
  return typedKey(draft) !== "" || endpointChanged(draft, provider);
}

/**
 * The `POST /providers/{id}/verify` body for a draft: the typed key when there
 * is one, otherwise none, and the sidecar uses the saved key. `null` when
 * there is no key to check with — never for a keyless server.
 */
export function verifyRequest(
  draft: ProviderDraft,
  provider: ProviderInfo,
): VerifyRequest | null {
  const endpoint = provider.takes_endpoint
    ? { base_url: normalizeEndpoint(draft.baseUrl) }
    : {};
  // A keyless server is always checkable: listing it needs no key (#88).
  if (!provider.requires_key) return endpoint;
  const apiKey = draft.apiKey.trim();
  if (draft.clearKey || (!apiKey && !hasSavedKey(provider))) return null;
  return { ...(apiKey ? { api_key: apiKey } : {}), ...endpoint };
}

/** Switch a model on (last in the list) or off, keeping the others' order:
 *  the first is the one its provider runs when chosen without a model. */
export function toggleModel(draft: ProviderDraft, model: string, on: boolean): ProviderDraft {
  if (!on) return { ...draft, enabled: draft.enabled.filter((name) => name !== model) };
  if (draft.enabled.includes(model)) return { ...draft, enabled: [...draft.enabled] };
  return { ...draft, enabled: [...draft.enabled, model] };
}

/** A model the endpoint doesn't list — a local server's, one the filter hid
 *  wrongly — added by name. */
export function addCustomModel(draft: ProviderDraft, name: string): ProviderDraft {
  const model = name.trim();
  if (!model || draft.enabled.includes(model)) return { ...draft };
  return { ...draft, enabled: [...draft.enabled, model] };
}

/**
 * The models switched on after a key's first listing: its suggestions that the
 * key can use, so a fresh key offers something in the pickers straight away,
 * or else the first model listed. A list the user already made is kept.
 */
export function seedEnabled(
  draft: ProviderDraft,
  provider: ProviderInfo,
  listed: string[],
): ProviderDraft {
  if (draft.enabled.length > 0 || listed.length === 0) return draft;
  const suggested = provider.suggested_models.filter((model) => listed.includes(model));
  return { ...draft, enabled: suggested.length > 0 ? suggested : [listed[0]] };
}

export interface ModelRow {
  id: string;
  enabled: boolean;
  /** `custom`: switched on, but no list has it. `hidden`: the non-chat
   *  filter took it out (`providers/listing.py`). */
  kind: "listed" | "suggested" | "hidden" | "custom";
}

function matchesEveryWord(id: string, search: string): boolean {
  const haystack = id.toLowerCase();
  return search
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}

/**
 * The card's model list. What is switched on but listed nowhere comes first,
 * so a name added by hand can't get lost below a hundred others; the hidden
 * models only with "show all", unless one is switched on.
 */
export function modelRows(
  catalog: ModelCatalog | undefined,
  draft: ProviderDraft,
  options: { search: string; showAll: boolean },
): ModelRow[] {
  const enabled = new Set(draft.enabled);
  const offered = (catalog?.models ?? []).filter((record) => record.source !== "saved");
  const hidden = catalog?.hidden ?? [];
  const known = new Set([...offered, ...hidden].map((record) => record.id));

  const rows: ModelRow[] = [
    ...draft.enabled
      .filter((id) => !known.has(id))
      .map((id) => ({ id, enabled: true, kind: "custom" as const })),
    ...offered.map((record) => ({
      id: record.id,
      enabled: enabled.has(record.id),
      kind: record.source === "suggested" ? ("suggested" as const) : ("listed" as const),
    })),
    ...hidden
      .filter((record) => options.showAll || enabled.has(record.id))
      .map((record) => ({ id: record.id, enabled: enabled.has(record.id), kind: "hidden" as const })),
  ];
  return rows.filter((row) => matchesEveryWord(row.id, options.search));
}

export function timeAgo(iso: string, now: number): string {
  const seconds = Math.max(0, (now - Date.parse(iso)) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

function countModels(catalog: ModelCatalog): string {
  const count = catalog.models.filter((record) => record.source === "listed").length;
  return `${count} ${count === 1 ? "model" : "models"}`;
}

export interface StatusLine {
  tone: "ok" | "error" | "muted";
  text: string;
}

/** The line under a card's name: where its key stands. */
export function statusLine(
  provider: ProviderInfo,
  catalog: ModelCatalog | undefined,
  now: number,
): StatusLine {
  if (!provider.requires_key) {
    // A keyless server's listing says whether it is up (#88).
    if (catalog?.error) return { tone: "error", text: catalog.error };
    if (catalog) return { tone: "ok", text: countModels(catalog) };
    return { tone: "ok", text: "No key needed" };
  }
  if (provider.key_state === "unreadable") {
    return {
      tone: "error",
      text: "The saved key couldn't be read (the keystore is locked). Enter it again.",
    };
  }
  if (!provider.has_key) return { tone: "muted", text: "No API key saved" };
  if (catalog?.error) return { tone: "error", text: catalog.error };
  if (provider.key_state === "invalid") {
    return { tone: "error", text: `${provider.label} rejected the saved key.` };
  }
  if (provider.key_state === "valid") {
    const checked = provider.last_verified_at
      ? ` · checked ${timeAgo(provider.last_verified_at, now)}`
      : "";
    if (!catalog) return { tone: "ok", text: `Verified${checked}` };
    return { tone: "ok", text: `${countModels(catalog)}${checked}` };
  }
  return { tone: "muted", text: "Not verified" };
}

/** Where Settings was opened from, for a provider's card: the toolbar's
 *  translation picker or the chat header's. */
export type OpenedFrom = "translation" | "answer";

interface SelectionInput {
  /** As it was before the save. */
  provider: ProviderInfo;
  update: ProviderUpdate;
  openedFor?: string | null;
  openedFrom?: OpenedFrom;
  savedAnyway: boolean;
  /** The model selecting the provider would choose: the one it runs. */
  model: string;
  /** What the new key's check listed, hidden models included. */
  listed: readonly string[];
}

/** Whether a model name is one of the listed ids, allowing for aliases, as
 *  `providers/listing.py:model_matches` does: Anthropic lists only
 *  `claude-haiku-4-5-20251001` for the alias `claude-haiku-4-5`, and Ollama
 *  `llama3.2:latest` for `llama3.2`. An exact match missed those and left an
 *  upgraded user on Argos after saving a key that works. */
function modelListed(model: string, listed: readonly string[]): boolean {
  return listed.some(
    (id) => id === model || (id.startsWith(model) && "-:".includes(id.charAt(model.length))),
  );
}

/** A new key that just listed its models, the model to choose among them:
 *  never on "Save anyway", which saves a key the provider turned down, and
 *  never onto a model the key can't use — a translator that fails every
 *  paragraph, where Argos would have kept working. */
function workingNewKey(input: SelectionInput): boolean {
  return (
    !!input.update.api_key && !input.savedAnyway && modelListed(input.model, input.listed)
  );
}

/** Opened from a picker's "Add an API key to use X…" for a provider with no
 *  key: the user came to use it there. */
function openedToAdd(input: SelectionInput, from: OpenedFrom): boolean {
  return (
    input.openedFor === input.provider.id &&
    !input.provider.has_key &&
    (input.openedFrom ?? "translation") === from
  );
}

/**
 * Whether saving a card also makes its provider the translation provider:
 * with a working new key (`workingNewKey`), when translation is still on the
 * offline engine — an LLM wins once it has one, as the sidecar did itself for
 * keys saved through `PUT /config` — or when Settings was opened from the
 * toolbar picker to add this provider's key.
 */
export function selectsProvider(input: SelectionInput & { translationProvider: string }): boolean {
  if (!workingNewKey(input) || input.translationProvider === input.provider.id) return false;
  return input.translationProvider === OFFLINE_ENGINE || openedToAdd(input, "translation");
}

/** Whether saving a card makes its provider answer in chat: Settings was
 *  opened from the chat header's picker to add its key, and the key works. */
export function selectsAnswerModel(input: SelectionInput): boolean {
  return workingNewKey(input) && openedToAdd(input, "answer");
}
