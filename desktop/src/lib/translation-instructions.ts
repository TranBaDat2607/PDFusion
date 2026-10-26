/**
 * Settings → Translation's instructions box, and whether they will reach the
 * model. Pure so the suite's `node` environment can test it.
 */

import type { ConfigResponse } from "@/hooks/useConfig";
import type { ProviderInfo } from "@/hooks/useProviders";
import { effectiveService } from "@/lib/translate-request";

/** `MAX_TRANSLATION_INSTRUCTIONS_CHARS` in `config/models.py`: the instruction
 *  rides on every paragraph's prompt, so its length is paid once per paragraph. */
export const MAX_INSTRUCTIONS_CHARS = 2000;

export interface InstructionsDraft {
  /** What `PUT /config` is sent: trimmed, `null` to clear. */
  value: string | null;
  dirty: boolean;
  tooLong: boolean;
}

/** The sidecar trims and stores a blank instruction as none, so the draft is
 *  compared in that form — whitespace alone is never an unsaved change. */
export function instructionsDraft(
  draft: string,
  saved: string | null | undefined,
): InstructionsDraft {
  const trimmed = draft.trim();
  const value = trimmed === "" ? null : trimmed;
  return {
    value,
    dirty: value !== (saved ?? null),
    tooLong: trimmed.length > MAX_INSTRUCTIONS_CHARS,
  };
}

type ApplyConfig = {
  translation: Pick<ConfigResponse["translation"], "model" | "instructions">;
};
type ApplyProvider = Pick<ProviderInfo, "id" | "is_llm" | "requires_key" | "has_key">;

/** Whether the service that will really run prompts a model. An LLM with no
 *  key runs as Argos, which takes no instructions. */
export function translatesWithLlm(
  config: { translation: Pick<ConfigResponse["translation"], "model"> },
  providers: readonly ApplyProvider[],
): boolean {
  const running = effectiveService(config, providers);
  return providers.find((p) => p.id === running)?.is_llm ?? false;
}

/** Whether a translation started now would send the instructions. Mirrors
 *  `capabilities.translation_instructions`. */
export function instructionsApply(
  config: ApplyConfig,
  providers: readonly ApplyProvider[],
): boolean {
  return !!config.translation.instructions && translatesWithLlm(config, providers);
}
