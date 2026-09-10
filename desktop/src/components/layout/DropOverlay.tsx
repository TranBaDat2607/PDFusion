import { FileDown, FileX } from "lucide-react";

import type { FileDropState } from "@/hooks/useFileDrop";
import { basename } from "@/lib/export-pdf";
import { cn } from "@/lib/utils";

/** Shown over the whole window while files are dragged onto it. It never
 *  takes pointer events: the drop itself is reported by the shell, not the
 *  page. */
export function DropOverlay({ state }: { state: FileDropState }) {
  if (state.kind === "idle") return null;
  const pdf = state.kind === "pdf";

  return (
    <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-sm">
      <div
        className={cn(
          "flex max-w-sm flex-col items-center gap-2 rounded-xl border-2 border-dashed px-10 py-8 text-center",
          pdf
            ? "border-primary bg-primary/5"
            : "border-destructive/60 bg-destructive/5",
        )}
      >
        {pdf ? (
          <FileDown className="h-6 w-6 text-primary" />
        ) : (
          <FileX className="h-6 w-6 text-destructive" />
        )}
        <p className="text-sm font-medium">
          {pdf ? "Drop to open" : "Only PDF files can be opened"}
        </p>
        {state.kind === "pdf" && (
          <p className="max-w-full truncate text-xs text-muted-foreground">
            {basename(state.path)}
          </p>
        )}
      </div>
    </div>
  );
}
