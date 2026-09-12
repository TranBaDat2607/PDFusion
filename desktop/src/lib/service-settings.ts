/**
 * A Settings service tab's draft, and what saving it takes (#32).
 *
 * Pure, so the rules for what `PUT /config` is sent, what Save checks with the
 * provider first, and when the sidecar would refuse the save outright all run
 * under vitest's node environment, the same reason `translate-request.ts`
 * exists.
 */

import type {
  ConfigResponse,
  ServiceCode,
  ValidateRequest,
} from "@/hooks/useConfig";
import type { components } from "@/lib/api-types";

type ConfigUpdate = components["schemas"]["ConfigUpdateRequest"];

export type LlmServiceCode = Exclude<ServiceCode, "argos">;

export const LLM_SERVICES: readonly LlmServiceCode[] = [
  "openai",
  "gemini",
  "anthropic",
];

/** The services that can be pointed at another server speaking their API:
 *  Ollama, LM Studio, a proxy. Mirrors `routes/config.py:_ENDPOINT_SERVICES`. */
const ENDPOINT_SERVICES: readonly LlmServiceCode[] = ["openai", "anthropic"];

export interface ServiceDraft {
  /** A key typed to replace the saved one. Empty keeps the saved key. */
  apiKey: string;
  /** Clear was clicked: saving removes the saved key. */
  clearKey: boolean;
  model: string;
  /** Blank means the provider's own endpoint. */
  baseUrl: string;
}

export type ServiceDrafts = Record<LlmServiceCode, ServiceDraft>;

/** The services as `GET /config` reports them. */
export type SavedServices = Pick<ConfigResponse, LlmServiceCode>;

export function draftsFrom(saved: SavedServices): ServiceDrafts {
  const draft = (code: LlmServiceCode): ServiceDraft => ({
    apiKey: "",
    clearKey: false,
    model: saved[code].model,
    baseUrl: saved[code].base_url ?? "",
  });
  return {
    openai: draft("openai"),
    gemini: draft("gemini"),
    anthropic: draft("anthropic"),
  };
}

export function takesEndpoint(code: LlmServiceCode): boolean {
  return ENDPOINT_SERVICES.includes(code);
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
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      url.hostname !== ""
    );
  } catch {
    return false;
  }
}

function endpointChanged(
  code: LlmServiceCode,
  draft: ServiceDraft,
  saved: SavedServices,
): boolean {
  return (
    takesEndpoint(code) &&
    normalizeEndpoint(draft.baseUrl) !== (saved[code].base_url ?? "")
  );
}

/**
 * Whether saving would change the endpoint of a service whose saved key is not
 * being replaced. The sidecar refuses that with a 422: a saved key is only ever
 * sent to the endpoint it was saved for, so it has to be typed again.
 */
export function endpointNeedsKey(
  code: LlmServiceCode,
  draft: ServiceDraft,
  saved: SavedServices,
): boolean {
  return (
    endpointChanged(code, draft, saved) &&
    saved[code].has_key &&
    !draft.clearKey &&
    !draft.apiKey.trim()
  );
}

/** Why this draft can't be saved as it stands, or null. */
export function draftProblem(
  code: LlmServiceCode,
  draft: ServiceDraft,
  saved: SavedServices,
): string | null {
  if (!draft.model.trim()) return "Enter a model name.";
  if (!isValidEndpoint(draft.baseUrl)) {
    return "The endpoint must be an http:// or https:// URL.";
  }
  if (endpointNeedsKey(code, draft, saved)) {
    return "Enter the API key again for the new endpoint. A saved key is only sent to the endpoint it was saved for.";
  }
  return null;
}

/** The `PUT /config` body: only what changed. */
export function buildConfigUpdate(
  drafts: ServiceDrafts,
  saved: SavedServices,
): ConfigUpdate {
  const update: ConfigUpdate = {};
  for (const code of LLM_SERVICES) {
    const draft = drafts[code];
    const change: { api_key?: string; model?: string; base_url?: string } = {};
    // `""` clears a key. `null` leaves it alone, and is what Clear used to
    // send, so a cleared key stayed saved.
    if (draft.clearKey) change.api_key = "";
    else if (draft.apiKey.trim()) change.api_key = draft.apiKey.trim();
    const model = draft.model.trim();
    if (model && model !== saved[code].model) change.model = model;
    if (endpointChanged(code, draft, saved)) {
      change.base_url = normalizeEndpoint(draft.baseUrl);
    }
    if (Object.keys(change).length > 0) update[code] = change;
  }
  return update;
}

/**
 * The `POST /config/validate` body for a draft. The typed key when there is
 * one; otherwise the sidecar uses the saved key. `null` when there is no key
 * to check with.
 */
export function validateRequestFor(
  code: LlmServiceCode,
  draft: ServiceDraft,
  saved: SavedServices,
): ValidateRequest | null {
  const apiKey = draft.apiKey.trim();
  const model = draft.model.trim();
  if (draft.clearKey || (!apiKey && !saved[code].has_key)) return null;
  return {
    service: code,
    ...(apiKey ? { api_key: apiKey } : {}),
    ...(model ? { model } : {}),
    ...(takesEndpoint(code) ? { base_url: normalizeEndpoint(draft.baseUrl) } : {}),
  };
}

/** What Save checks with the provider first: every service whose key, model
 *  or endpoint changes and that still has a key to check with. */
export function servicesToProbe(
  drafts: ServiceDrafts,
  saved: SavedServices,
): Array<{ code: LlmServiceCode; request: ValidateRequest }> {
  const probes: Array<{ code: LlmServiceCode; request: ValidateRequest }> = [];
  for (const code of LLM_SERVICES) {
    const draft = drafts[code];
    const model = draft.model.trim();
    const changed =
      draft.apiKey.trim() !== "" ||
      (model !== "" && model !== saved[code].model) ||
      endpointChanged(code, draft, saved);
    const request = changed ? validateRequestFor(code, draft, saved) : null;
    if (request) probes.push({ code, request });
  }
  return probes;
}
