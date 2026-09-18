import { useEffect, useState } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTranslationEstimate } from "@/hooks/useTranslationEstimate";
import { parsePageRanges } from "@/lib/page-range";
import { useAppStore } from "@/lib/store";
import { estimateDetails, estimateLabel } from "@/lib/usage-estimate";

/** How long the Pages box must sit still before it is estimated again, so
 *  typing "1-20" doesn't ask about "1", then "1-" (every page), then "1-2". */
const TYPING_PAUSE_MS = 400;

interface TokenEstimateProps {
  filePath: string;
  targetLang: string;
}

/**
 * "≈12k tokens" beside the model name, when an LLM will run (#33). Nothing is
 * shown while the first answer loads, or when the sidecar can't estimate —
 * the Pages box and Translate report those problems already.
 */
export function TokenEstimate({ filePath, targetLang }: TokenEstimateProps) {
  const text = useAppStore((s) => s.pageRangeText);
  const pageCount = useAppStore((s) => s.originalPageCount);
  const [settled, setSettled] = useState(text);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(text), TYPING_PAUSE_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const parsed = parsePageRanges(settled, pageCount);
  const estimate = useTranslationEstimate({
    filePath,
    ranges: parsed.ok ? parsed.ranges : null,
    targetLang,
    enabled: parsed.ok,
  });
  if (!estimate.data || estimate.isError) return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className="rounded-md bg-muted px-2 py-1 text-[10px] tabular-nums text-muted-foreground"
        >
          {estimateLabel(estimate.data)}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <ul className="space-y-1">
          {estimateDetails(estimate.data).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}
