import { useState } from "react";

import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { describeSelection, parsePageRanges } from "@/lib/page-range";
import { useAppStore } from "@/lib/store";

const HELP = "Pages to translate, like 1-20, 35. Leave it empty for all of them.";

interface PageRangeInputProps {
  disabled: boolean;
  /** Enter in the box: start translating. */
  onSubmit: () => void;
}

/**
 * The toolbar's Pages box (#33). What's typed lives in the store, parsed where
 * it's read (`lib/page-range.ts`), so the toolbar and the Translate handler
 * can't disagree about it.
 */
export function PageRangeInput({ disabled, onSubmit }: PageRangeInputProps) {
  const text = useAppStore((s) => s.pageRangeText);
  const setText = useAppStore((s) => s.setPageRangeText);
  const pageCount = useAppStore((s) => s.originalPageCount);
  const [hoverOpen, setHoverOpen] = useState(false);
  const [focused, setFocused] = useState(false);

  const parsed = parsePageRanges(text, pageCount);
  const hint = describeSelection(parsed, pageCount);
  const error = parsed.ok ? null : parsed.error;

  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor="page-range" className="text-xs text-muted-foreground">
        Pages
      </label>
      {/* Radix opens a tooltip on hover and on keyboard focus, but not when a
          click focuses the field — so a mistake is kept on screen while the
          user is still typing in it. */}
      <Tooltip
        open={hoverOpen || (focused && error !== null)}
        onOpenChange={setHoverOpen}
      >
        <TooltipTrigger asChild>
          <Input
            id="page-range"
            value={text}
            placeholder="All"
            spellCheck={false}
            autoComplete="off"
            disabled={disabled}
            aria-invalid={error !== null || undefined}
            onChange={(e) => setText(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && error === null) onSubmit();
            }}
            className="h-8 w-28 px-2 text-xs md:text-xs"
          />
        </TooltipTrigger>
        <TooltipContent>{error ?? HELP}</TooltipContent>
      </Tooltip>
      {hint && (
        <span className="text-xs tabular-nums text-muted-foreground">{hint}</span>
      )}
    </div>
  );
}
