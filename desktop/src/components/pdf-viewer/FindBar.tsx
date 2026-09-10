import { useEffect, useRef } from "react";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface FindBarProps {
  query: string;
  onQueryChange: (query: string) => void;
  matchCount: number;
  /** Index of the current match, or `null` before one is picked. */
  selected: number | null;
  /** Pages are still being read, so the count may grow. */
  searching: boolean;
  /** Changes whenever the bar is asked for (again): take focus and select the
   *  query, so Ctrl+F then typing replaces it. */
  focusKey: number;
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
}

function statusText(
  query: string,
  matchCount: number,
  selected: number | null,
  searching: boolean,
): string {
  if (!query.trim()) return "";
  if (matchCount === 0) return searching ? "Searching…" : "No results";
  const position = selected === null ? "–" : String(selected + 1);
  return `${position} / ${matchCount}${searching ? "+" : ""}`;
}

export function FindBar({
  query,
  onQueryChange,
  matchCount,
  selected,
  searching,
  focusKey,
  onNext,
  onPrevious,
  onClose,
}: FindBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [focusKey]);

  return (
    <div
      role="search"
      className="absolute right-3 top-3 z-20 flex items-center gap-0.5 rounded-lg border border-border bg-background/95 p-1 shadow-lg backdrop-blur"
    >
      <Search className="ml-1.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <Input
        ref={inputRef}
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Enter") {
            event.preventDefault();
            if (event.shiftKey) onPrevious();
            else onNext();
          } else if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
        placeholder="Find in document"
        aria-label="Find in document"
        spellCheck={false}
        className="h-7 w-44 border-0 bg-transparent px-1.5 text-xs shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <span
        aria-live="polite"
        className="min-w-14 px-1 text-center font-mono text-[11px] tabular-nums text-muted-foreground"
      >
        {statusText(query, matchCount, selected, searching)}
      </span>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        onClick={onPrevious}
        disabled={matchCount === 0}
        aria-label="Previous match"
        title="Previous match (Shift+Enter)"
      >
        <ChevronUp className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        onClick={onNext}
        disabled={matchCount === 0}
        aria-label="Next match"
        title="Next match (Enter)"
      >
        <ChevronDown className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        onClick={onClose}
        aria-label="Close find"
        title="Close (Esc)"
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
