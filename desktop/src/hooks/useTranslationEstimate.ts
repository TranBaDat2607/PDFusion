import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import type { PageRange } from "@/lib/page-range";
import type { TranslationEstimate } from "@/lib/usage-estimate";

type EstimateRequest = components["schemas"]["EstimateRequest"];

interface EstimateInput {
  filePath: string | null;
  /** The Pages box, parsed; `null` for every page. */
  ranges: PageRange[] | null;
  targetLang: string | null;
  /** Only while an LLM will run: Argos costs nothing to call. */
  enabled: boolean;
}

/**
 * Roughly how many tokens translating these pages would take (#33). The file,
 * pages and language are the whole question, so an answer is kept for the
 * session; while a new one loads, the previous one stays on screen.
 */
export function useTranslationEstimate({
  filePath,
  ranges,
  targetLang,
  enabled,
}: EstimateInput) {
  return useQuery({
    queryKey: ["translate", "estimate", filePath, ranges, targetLang],
    queryFn: () => {
      const body: EstimateRequest = {
        file_path: filePath as string,
        ...(ranges ? { page_ranges: ranges } : {}),
        ...(targetLang
          ? { target_lang: targetLang as EstimateRequest["target_lang"] }
          : {}),
      };
      return api.post<TranslationEstimate>("/translate/estimate", body);
    },
    enabled: enabled && filePath !== null,
    staleTime: Infinity,
    retry: false,
    placeholderData: keepPreviousData,
  });
}
