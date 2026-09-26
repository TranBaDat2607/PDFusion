/**
 * The model pickers — the toolbar's translation model and the chat header's
 * answer model (#87): what they offer, what the toolbar button says, and what
 * picking an entry saves.
 *
 * The service used to be a toolbar Select and the model a field in Settings,
 * so changing the model was a trip through the sheet, and the toolbar went on
 * naming a keyless LLM while Argos ran in its place. Pure, so the rules run
 * under vitest's node environment like `translate-request.ts`.
 *
 * Every provider fact comes from `GET /providers`, which reads the registry
 * (`providers/registry.py`); nothing here names a provider (#86).
 */

import type { ConfigResponse, ConfigUpdate } from "@/hooks/useConfig";
import type { ProviderInfo } from "@/hooks/useProviders";
import { canRun, effectiveService } from "@/lib/translate-request";

export type PickerProvider = Pick<
  ProviderInfo,
  | "id"
  | "label"
  | "short_label"
  | "requires_key"
  | "has_key"
  | "takes_endpoint"
  | "base_url"
  | "default_model"
  | "model"
  | "enabled_models"
  | "model_is_fixed"
  | "is_llm"
  | "priority"
>;

export type ChoiceConfig = {
  translation: Pick<ConfigResponse["translation"], "model">;
  rag: Pick<ConfigResponse["rag"], "answer_model">;
};

type ModelRef = ConfigResponse["translation"]["model"];

export interface ModelEntry {
  model: string;
  /** The provider's default model, on its own endpoint. */
  isDefault: boolean;
}

export interface ModelGroup {
  id: string;
  label: string;
  /** Can run now: it has a key, or takes none. */
  usable: boolean;
  /** Takes a key and has none: offers "Add an API key", not its models. */
  needsKey: boolean;
  /** Takes no key: usable, though nothing says a local server is running. */
  keyless: boolean;
  /** Its model is a fixed identifier (Argos): nothing to choose. */
  fixed: boolean;
  /** Writes text from a prompt, so it can answer in chat. */
  llm: boolean;
  /** The server it was pointed at in place of the provider's own. */
  endpoint: string | null;
  models: ModelEntry[];
}

/**
 * The models a provider offers: the ones switched on for it in Settings →
 * Models (#86), in order. The translation and answer models stay on offer
 * even once switched off, so the picker can always show what is chosen; and
 * with none switched on, the model it runs, so a provider with a key always
 * has one to pick.
 */
function offeredModels(provider: PickerProvider, config: ChoiceConfig): string[] {
  if (provider.model_is_fixed) return [provider.default_model];
  const chosen = [config.translation.model, config.rag.answer_model]
    .filter((ref): ref is ModelRef => !!ref && ref.provider === provider.id)
    .map((ref) => ref.model);
  const names = [...provider.enabled_models, ...chosen];
  return names.length > 0 ? [...new Set(names)] : [provider.model];
}

export function modelGroups(
  config: ChoiceConfig,
  providers: readonly PickerProvider[],
): ModelGroup[] {
  return providers.map((provider) => {
    const endpoint = (provider.takes_endpoint && provider.base_url) || null;
    return {
      id: provider.id,
      label: provider.label,
      usable: canRun(provider),
      needsKey: provider.requires_key && !provider.has_key,
      keyless: !provider.requires_key,
      fixed: provider.model_is_fixed,
      llm: provider.is_llm,
      endpoint,
      models: offeredModels(provider, config).map((model) => ({
        model,
        isDefault: !endpoint && model === provider.default_model,
      })),
    };
  });
}

/** Whether this entry is what Translate would run now. */
export function isCurrent(config: ChoiceConfig, provider: string, model: string): boolean {
  const current = config.translation.model;
  return current.provider === provider && current.model === model;
}

/** The `PUT /config` body for picking an entry: provider and model in one
 *  reference, so the toolbar never shows one without the other. */
export function selectionUpdate(
  config: ChoiceConfig,
  provider: string,
  model: string,
): ConfigUpdate {
  if (isCurrent(config, provider, model)) return {};
  return {
    translation_model: { provider, model } as NonNullable<ConfigUpdate["translation_model"]>,
  };
}

export interface PickerSummary {
  /** The provider that will run, short name. */
  service: string;
  /** Its model, or null for Argos. */
  model: string | null;
  /** Set when the selected LLM has no key and Argos runs instead: why. */
  downgradedFrom: string | null;
}

const shortLabel = (providers: readonly PickerProvider[], id: string) =>
  providers.find((p) => p.id === id)?.short_label ?? id;

/**
 * What the picker's button says. It names the provider that will *run*: an
 * LLM with no key is swapped for Argos by the sidecar, and the button used to
 * go on naming the LLM and its model through the whole Argos run.
 */
export function pickerSummary(
  config: ChoiceConfig,
  providers: readonly PickerProvider[],
): PickerSummary {
  const requested = config.translation.model;
  const running = effectiveService(config, providers);
  const fixed = providers.find((p) => p.id === running)?.model_is_fixed ?? true;
  return {
    service: shortLabel(providers, running),
    model: fixed ? null : requested.model,
    downgradedFrom: running === requested.provider ? null : shortLabel(providers, requested.provider),
  };
}

/** The provider Settings opens on for "Custom model or endpoint…": the one
 *  translating, or from a fixed-model engine the first that takes an
 *  endpoint, since a model of one's own needs a server. */
export function settingsTargetFor(
  config: ChoiceConfig,
  providers: readonly PickerProvider[],
): string {
  const current = providers.find((p) => p.id === config.translation.model.provider);
  if (current && !current.model_is_fixed) return current.id;
  return (providers.find((p) => p.takes_endpoint) ?? providers[0]).id;
}

export interface AnsweringModel {
  provider: string;
  /** Short name, for the chat header. */
  label: string;
  model: string;
}

/**
 * The LLM that writes chat answers, mirroring
 * `rag/rag_chain.py:EnhancedRAGChain._answer_model`: the answer model picked
 * in the chat header, then the translation model, then every LLM by
 * `priority` with the model it runs — the first whose provider is an LLM with
 * a key. `null` with no key at all, when chat answers with excerpts from the
 * document instead.
 */
export function chatModel(
  config: ChoiceConfig,
  providers: readonly PickerProvider[],
): AnsweringModel | null {
  // Any LLM answers when chosen; only one with a priority is a fallback, so a
  // keyless local server that may not be running is never tried unasked (#88).
  const llms = providers.filter((p) => p.is_llm);
  const byPriority = llms
    .filter((p) => p.priority !== null && p.priority !== undefined)
    .sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0));
  const candidates: ModelRef[] = [
    ...(config.rag.answer_model ? [config.rag.answer_model] : []),
    config.translation.model,
    ...byPriority.map((p) => ({ provider: p.id, model: p.model }) as ModelRef),
  ];
  for (const ref of candidates) {
    const provider = llms.find((p) => p.id === ref.provider);
    if (provider && canRun(provider)) {
      return { provider: provider.id, label: provider.short_label, model: ref.model };
    }
  }
  return null;
}

/**
 * The model that would answer after "Same as translation", shown beside it in
 * the chat header. Not simply the translation model: the offline engine can't
 * answer and a provider with no key is skipped, and naming either there put
 * the menu at odds with the header, which shows the model really answering.
 */
export function followingModel(
  config: ChoiceConfig,
  providers: readonly PickerProvider[],
): AnsweringModel | null {
  return chatModel({ ...config, rag: { ...config.rag, answer_model: null } }, providers);
}

/** A chat header pick: a model, or `null` for "Same as translation". */
export type AnswerChoice = { provider: string; model: string } | null;

/** Whether this chat header entry is the saved choice. */
export function isCurrentAnswer(config: ChoiceConfig, choice: AnswerChoice): boolean {
  const saved = config.rag.answer_model;
  if (!saved || !choice) return !saved && !choice;
  return saved.provider === choice.provider && saved.model === choice.model;
}

/**
 * The `PUT /config` body for a chat header pick. A model is pinned even when
 * it is the translation model's, so it stays when translation changes;
 * "Same as translation" is `null`, which follows it. The sidecar reads the
 * choice afresh for every question, so it applies from the next one.
 */
export function answerModelUpdate(config: ChoiceConfig, choice: AnswerChoice): ConfigUpdate {
  if (isCurrentAnswer(config, choice)) return {};
  return {
    answer_model: choice as ConfigUpdate["answer_model"],
  };
}

/** The small print under an answer: which model wrote it, so a conversation
 *  that changed models part-way stays readable. `null` when none did. */
export function answeredBy(
  providers: readonly Pick<PickerProvider, "id" | "short_label">[],
  provider: string | null | undefined,
  model: string | null | undefined,
): string | null {
  if (!provider || !model) return null;
  const label = providers.find((p) => p.id === provider)?.short_label ?? provider;
  return `${label} · ${model}`;
}
