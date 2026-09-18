/**
 * The token estimate beside Translate when an LLM will run (#33): its label,
 * and the lines its tooltip shows. The numbers come from
 * `POST /translate/estimate` (`translators/usage_estimate.py`). Pure, so it
 * runs under vitest's node environment.
 */

import type { components } from "@/lib/api-types";
import { pluralizePages } from "@/lib/translation-progress";

export type TranslationEstimate = components["schemas"]["TranslationEstimate"];

/** 950 → "950", 12 345 → "12k", 1 234 567 → "1.2M". Rounded up, like the
 *  estimate itself: it is there to say "at most about this much". */
export function formatTokens(tokens: number): string {
  if (tokens < 1000) return `${tokens}`;
  const tenthsOfK = Math.ceil(tokens / 100);
  if (tenthsOfK < 100) return `${oneDecimal(tenthsOfK / 10)}k`;
  const k = Math.ceil(tokens / 1000);
  if (k < 1000) return `${k}k`;
  return `${oneDecimal(Math.ceil(tokens / 100_000) / 10)}M`;
}

const oneDecimal = (n: number) => n.toFixed(1).replace(/\.0$/, "");

export function estimateLabel(estimate: TranslationEstimate): string {
  return `≈${formatTokens(estimate.input_tokens + estimate.output_tokens)} tokens`;
}

export function estimateDetails(estimate: TranslationEstimate): string[] {
  const paragraphs = `${estimate.paragraphs} paragraph${estimate.paragraphs === 1 ? "" : "s"}`;
  return [
    `About ${paragraphs} on ${pluralizePages(estimate.pages_selected)}, each sent to the service on its own.`,
    `≈${formatTokens(estimate.input_tokens)} tokens sent, instructions included; ≈${formatTokens(estimate.output_tokens)} back.`,
    "A rough count from the PDF's text. The service's own count will differ.",
  ];
}
