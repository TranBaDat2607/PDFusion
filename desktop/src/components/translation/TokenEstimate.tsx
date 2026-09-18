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
  // The Pages box belongs to a document, so what was settled records which.
  // `setOriginalPdfPath` clears the box in the same tick the path changes, so
  // a pause left over from the previous document would spend TYPING_PAUSE_MS
  // holding *its* pages against this one — a wrong number on screen, or a 422
  // if this document is shorter. Re-anchor the pause to the new document, and
  // until that has landed read the box as it stands.
  const [settled, setSettled] = useState({ path: filePath, text });
  if (settled.path !== filePath) setSettled({ path: filePath, text });
  const pages = settled.path === filePath ? settled.text : text;
  useEffect(() => {
    const timer = window.setTimeout(
      () => setSettled({ path: filePath, text }),
      TYPING_PAUSE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [filePath, text]);

  const parsed = parsePageRanges(pages, pageCount);
  const estimate = useTranslationEstimate({
    filePath,
    ranges: parsed.ok ? parsed.ranges : null,
    targetLang,
    enabled: parsed.ok,
  });
  // `enabled: false` does not hide what is already cached, and an unreadable
  // Pages box asks the same question as a blank one — so without the first
  // clause, typing garbage would show the whole document's cached answer as
  // if it were the answer for what was typed.
  if (!parsed.ok || !estimate.data || estimate.isError) return null;

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
