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
import type { ProviderInfo } from "@/hooks/useProviders";
import type { components } from "@/lib/api-types";
import type { PageRange } from "@/lib/page-range";

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
  /** The Pages box, parsed. Omitted when null/undefined — the whole document. */
  pageRanges?: PageRange[] | null;
}

type TranslateRequest = components["schemas"]["TranslateRequest"];

export function buildTranslateBody(input: TranslateBodyInput): TranslateRequest {
  // `sourceLang`/`targetLang`/`service` arrive as plain strings — the toolbar
  // dropdowns and the config values feeding them are typed loosely all the
  // way up (`LanguageOption.code`/`ServiceOption.code` are plain `string` in
  // `api/schemas.py` too, since GET /config/options is generic dropdown
  // data) — but every real value does come from `LanguageCode`/
  // `TranslationService` on the backend, so narrowing here is a boundary
  // cast, not an escape from the request's real contract.
  //
  // The three are genuinely omitted (not sent as `undefined`) when unset:
  // conditionally spreading them, rather than always assigning the key, keeps
  // this observable — see translate-request.test.ts's "omits unset languages"
  // case, which checks for the key's absence, not just an undefined value.
  return {
    file_path: input.filePath,
    visible_page: input.visiblePage,
    bypass_cache: input.bypassCache ?? false,
    ...(input.sourceLang
      ? { source_lang: input.sourceLang as TranslateRequest["source_lang"] }
      : {}),
    ...(input.targetLang
      ? { target_lang: input.targetLang as TranslateRequest["target_lang"] }
      : {}),
    ...(input.service
      ? { service: input.service as TranslateRequest["service"] }
      : {}),
    ...(input.pageRanges ? { page_ranges: input.pageRanges } : {}),
  };
}

/** What runs in place of an LLM that can't: the sidecar's fallback. */
export const OFFLINE_ENGINE = "argos";

type RunnableProvider = { id: string } & Pick<ProviderInfo, "requires_key" | "has_key">;

/** Whether a provider can run as saved, mirroring `AppSettings.has_api_key`:
 *  it has a key, or takes none. */
export function canRun(provider: Pick<RunnableProvider, "requires_key" | "has_key">): boolean {
  return !provider.requires_key || provider.has_key;
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
  config: { translation: Pick<ConfigResponse["translation"], "model"> },
  providers: readonly RunnableProvider[],
): ServiceCode {
  const requested = config.translation.model.provider;
  const provider = providers.find((p) => p.id === requested);
  return provider && canRun(provider) ? requested : OFFLINE_ENGINE;
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

/**
 * Whether `service` can translate from `sourceLang` into anything at all — the
 * toolbar's From list, which greys out the rest (#33). Same unrestricted cases
 * as `isPairSupported`.
 */
export function isSourceSupported(
  options: Pick<OptionsResponse, "services">,
  service: ServiceCode,
  sourceLang: string,
): boolean {
  const pairs = supportedPairs(options, service);
  if (pairs == null) return true;
  return pairs.some(([from]) => from === sourceLang);
}
