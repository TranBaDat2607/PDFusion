/**
 * The toolbar's model picker: what it offers, what its button says, and what
 * picking an entry saves.
 *
 * The service used to be a toolbar Select and the model a field in Settings,
 * so changing the model was a trip through the sheet, and the toolbar went on
 * naming a keyless LLM while Argos ran in its place. Pure, so the rules run
 * under vitest's node environment like `translate-request.ts`.
 */

import type {
  ConfigResponse,
  ConfigUpdate,
  OptionsResponse,
  ServiceCode,
} from "@/hooks/useConfig";
import { effectiveService } from "@/lib/translate-request";
import { LLM_SERVICES, takesEndpoint, type LlmServiceCode } from "@/lib/service-settings";

type Config = Pick<
  ConfigResponse,
  "translation" | "openai" | "gemini" | "anthropic" | "argos"
>;

/** Short names, for the toolbar and the Settings tab row, where the full ones
 *  ("Argos Translate (offline)") are too wide. */
export const SERVICE_SHORT_LABELS: Record<ServiceCode, string> = {
  argos: "Argos",
  openai: "OpenAI",
  gemini: "Gemini",
  anthropic: "Claude",
};

export interface ModelEntry {
  model: string;
  /** The app's default for the service: the first suggestion. */
  isDefault: boolean;
}

export interface ServiceGroup {
  code: ServiceCode;
  label: string;
  /** Argos needs none, so it always has one. */
  hasKey: boolean;
  /** The server a service was pointed at in place of the provider's own. */
  endpoint: string | null;
  models: ModelEntry[];
}

/**
 * Every service with the models it offers.
 *
 * A service pointed at another server offers what that server lists
 * (`endpointModels`, fetched by the picker) and not the provider's
 * suggestions, which such a server doesn't have. The saved model always
 * stays on offer, so a name typed in Settings never vanishes from the list.
 */
export function modelGroups(
  config: Config,
  options: Pick<OptionsResponse, "services">,
  endpointModels: Partial<Record<LlmServiceCode, string[]>> = {},
): ServiceGroup[] {
  return options.services.map((option) => {
    const code = option.code as ServiceCode;
    if (code === "argos") {
      return { code, label: option.label, hasKey: true, endpoint: null, models: [] };
    }
    const saved = config[code];
    const endpoint = (takesEndpoint(code) && saved.base_url) || null;
    const offered = endpoint ? (endpointModels[code] ?? []) : option.models;
    const names = [saved.model, ...offered.filter((m) => m !== saved.model)];
    // Suggestion order is the server's (default first); the saved model goes
    // first only when it's a name the list doesn't have.
    const ordered = offered.includes(saved.model) ? offered : names;
    return {
      code,
      label: option.label,
      hasKey: saved.has_key,
      endpoint,
      models: ordered.map((model) => ({
        model,
        isDefault: !endpoint && model === option.models[0],
      })),
    };
  });
}

/** Whether this entry is what Translate would run now. */
export function isCurrent(config: Config, code: ServiceCode, model: string | null): boolean {
  if (config.translation.preferred_service !== code) return false;
  return code === "argos" || config[code].model === model;
}

/**
 * The `PUT /config` body for picking an entry: only what changes. The
 * service and its model go in one request, so the toolbar never shows one
 * without the other.
 */
export function selectionUpdate(
  config: Config,
  code: ServiceCode,
  model: string | null,
): ConfigUpdate {
  const update: ConfigUpdate = {};
  if (config.translation.preferred_service !== code) update.preferred_service = code;
  if (code !== "argos" && model && config[code].model !== model) {
    update[code] = { model };
  }
  return update;
}

export interface PickerSummary {
  /** The service that will run, short name. */
  service: string;
  /** Its model, or null for Argos. */
  model: string | null;
  /** Set when the selected LLM has no key and Argos runs instead: why. */
  downgradedFrom: string | null;
}

/**
 * What the picker's button says. It names the service that will *run*: an
 * LLM with no key is swapped for Argos by the sidecar, and the button used to
 * go on naming the LLM and its model through the whole Argos run.
 */
export function pickerSummary(config: Config): PickerSummary {
  const requested = config.translation.preferred_service;
  const running = effectiveService(config);
  return {
    service: SERVICE_SHORT_LABELS[running],
    model: running === "argos" ? null : config[running].model,
    downgradedFrom: running === requested ? null : SERVICE_SHORT_LABELS[requested],
  };
}

/** The LLM service to open Settings on for "Custom model or endpoint…". */
export function settingsTabFor(config: Config): LlmServiceCode {
  const preferred = config.translation.preferred_service;
  return preferred === "argos" ? "openai" : preferred;
}

/** Services whose endpoint the picker asks for its model list. */
export function servicesToList(config: Config): LlmServiceCode[] {
  return LLM_SERVICES.filter(
    (code) => takesEndpoint(code) && !!config[code].base_url && config[code].has_key,
  );
}

// Order `rag_chain.py:_LLM_SERVICES` tries them in, which differs from the
// Settings tab order.
const CHAT_ORDER: readonly LlmServiceCode[] = ["openai", "anthropic", "gemini"];

/**
 * The LLM that writes chat answers, mirroring
 * `rag/rag_chain.py:EnhancedRAGChain._answer_model`: the preferred service
 * when it's an LLM with a key, else the first that has one. `null` with no
 * key at all, when chat answers with excerpts from the document instead.
 */
export function chatModel(
  config: Config,
): { service: LlmServiceCode; model: string } | null {
  const preferred = config.translation.preferred_service;
  const candidates: LlmServiceCode[] =
    preferred === "argos"
      ? [...CHAT_ORDER]
      : [preferred, ...CHAT_ORDER.filter((c) => c !== preferred)];
  const service = candidates.find((code) => config[code].has_key);
  return service ? { service, model: config[service].model } : null;
}
