import { useState } from "react";
import { Maximize2, Minus, Plus, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { parsePageInput } from "@/lib/pdf-viewer/layout";
import { cn } from "@/lib/utils";

interface ViewerToolbarProps {
  /** "Original" / "Translated". */
  label?: string;
  /** This pane receives the keyboard shortcuts. */
  active: boolean;
  hasDocument: boolean;
  pageCount: number;
  currentPage: number;
  onGoToPage: (page: number) => void;
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitWidth: () => void;
  onFind: () => void;
}

export function ViewerToolbar({
  label,
  active,
  hasDocument,
  pageCount,
  currentPage,
  onGoToPage,
  zoom,
  onZoomIn,
  onZoomOut,
  onFitWidth,
  onFind,
}: ViewerToolbarProps) {
  // What's typed in the page box while it has focus. `null` shows the page the
  // reader is on, so the box tracks scrolling until someone types in it.
  const [draft, setDraft] = useState<string | null>(null);

  return (
    <div className="flex h-9 shrink-0 items-center justify-between border-t border-border bg-background px-3 text-xs">
      <div className="flex items-center gap-2 text-muted-foreground">
        {label && (
          <span
            className={cn(
              "font-medium transition-colors",
              active && hasDocument && "text-foreground",
            )}
          >
            {label}
          </span>
        )}
        {hasDocument && (
          <div className="flex items-center gap-1">
            <input
              value={draft ?? String(currentPage)}
              onFocus={(event) => {
                setDraft(String(currentPage));
                event.currentTarget.select();
              }}
              onChange={(event) => setDraft(event.target.value)}
              onBlur={() => setDraft(null)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  const page = parsePageInput(draft ?? "", pageCount);
                  if (page !== null) onGoToPage(page);
                  event.currentTarget.blur();
                } else if (event.key === "Escape") {
                  event.currentTarget.blur();
                }
              }}
              inputMode="numeric"
              aria-label="Page number"
              title="Go to page"
              className="h-6 w-10 rounded border border-input bg-transparent text-center font-mono tabular-nums text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            />
            <span className="font-mono tabular-nums">/ {pageCount}</span>
          </div>
        )}
      </div>
      {hasDocument && (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onFind}
            aria-label="Find in document"
            title="Find (Ctrl+F)"
          >
            <Search className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onZoomOut}
            aria-label="Zoom out"
            title="Zoom out (Ctrl+−)"
          >
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <span className="min-w-[42px] text-center font-mono">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onZoomIn}
            aria-label="Zoom in"
            title="Zoom in (Ctrl++)"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onFitWidth}
            aria-label="Fit width"
            title="Fit width"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}
