/**
 * Building a `/translate` request, and working out which language pairs the
 * currently-selected backend can actually deliver.
 *
 * Split out of the hook and the toolbar for two reasons. It keeps the pure
 * decisions unit-testable under the suite's `node` environment (same reason
 * `export-pdf.ts` exists), and it puts the "which service will really run"
 * question in one place — the toolbar needs it to grey out targets, and the
 * translate call needs it to send the right thing.
 *
 * The capability data itself is *not* defined here: it arrives from
 * `GET /config/options` as `supported_pairs`, already expanded server-side
 * (see `translators/capabilities.py`). Duplicating the matrix in TypeScript is
 * how the two sides drift apart.
 */

import type { ConfigResponse, OptionsResponse, ServiceCode } from "@/hooks/useConfig";

export interface TranslateBodyInput {
  filePath: string;
  visiblePage: number;
  bypassCache?: boolean;
  /** Omitted from the body when null/undefined — the sidecar then applies the
   *  configured default. Never send a placeholder like "auto" to mean "unset":
   *  "auto" is a real, selectable source language. */
  sourceLang?: string | null;
  targetLang?: string | null;
  service?: string | null;
}

export function buildTranslateBody(
  input: TranslateBodyInput,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    file_path: input.filePath,
    visible_page: input.visiblePage,
    bypass_cache: input.bypassCache ?? false,
  };
  if (input.sourceLang) body.source_lang = input.sourceLang;
  if (input.targetLang) body.target_lang = input.targetLang;
  if (input.service) body.service = input.service;
  return body;
}

/**
 * The service that will really run, mirroring
 * `translators/capabilities.py:resolve_effective_service`.
 *
 * An LLM with no API key is silently downgraded to Argos by the sidecar, so a
 * toolbar reading "OpenAI" can still be an Argos run — and Argos only does
 * English → Vietnamese. Asking about the *requested* service here would offer
 * the user Japanese and then fail the job.
 */
export function effectiveService(
  config: Pick<ConfigResponse, "translation" | "openai" | "gemini" | "anthropic" | "argos">,
): ServiceCode {
  const requested = config.translation.preferred_service;
  if (requested === "argos") return "argos";
  return config[requested]?.has_key ? requested : "argos";
}

function supportedPairs(
  options: Pick<OptionsResponse, "services">,
  service: ServiceCode,
): string[][] | null | undefined {
  return options.services.find((s) => s.code === service)?.supported_pairs;
}

/**
 * Whether `service` can translate this pair.
 *
 * `null` pairs mean unrestricted (the LLMs). An unknown service — or options
 * from a sidecar too old to send `supported_pairs` — is treated as
 * unrestricted too: the server still pre-flights the request and answers 422,
 * so guessing "unsupported" here would only hide working combinations.
 */
export function isPairSupported(
  options: Pick<OptionsResponse, "services">,
  service: ServiceCode,
  sourceLang: string,
  targetLang: string,
): boolean {
  const pairs = supportedPairs(options, service);
  if (pairs == null) return true;
  return pairs.some(([from, to]) => from === sourceLang && to === targetLang);
}
