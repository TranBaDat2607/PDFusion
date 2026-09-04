import { Download, ExternalLink, FolderOpen, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useExportTranslated } from "@/hooks/useExportTranslated";

interface TranslatedFileActionsProps {
  /** Icon-only Open / Show buttons, for the crowded toolbar. */
  compact?: boolean;
}

/**
 * Save / Open / Show-in-folder for the current translation.
 *
 * Save is the primary action because everything the pipeline writes is
 * temporary — until the user picks a destination, there is no copy of the
 * translation that survives closing the app.
 */
export function TranslatedFileActions({
  compact = false,
}: TranslatedFileActionsProps) {
  const { saving, hasTranslation, exportedPath, saveAs, openFile, revealFile } =
    useExportTranslated();

  if (!hasTranslation) return null;

  const secondarySize = compact ? ("icon" as const) : ("sm" as const);
  const secondaryClass = compact ? "h-8 w-8" : "gap-2";
  const secondary = [
    {
      Icon: ExternalLink,
      label: "Open",
      tooltip: "Open in your default PDF viewer",
      onClick: openFile,
    },
    {
      Icon: FolderOpen,
      label: "Show in folder",
      tooltip: "Show the file in Explorer",
      onClick: revealFile,
    },
  ];

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Outline in the toolbar so it doesn't compete with Translate; solid in
          the completion overlay, where saving is the one thing left to do. */}
      <Button
        size="sm"
        variant={compact || exportedPath ? "outline" : "default"}
        className="gap-2"
        disabled={saving}
        onClick={() => void saveAs()}
      >
        {saving ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Download className="h-4 w-4" />
        )}
        {exportedPath ? "Save a copy…" : "Save PDF…"}
      </Button>

      {secondary.map(({ Icon, label, tooltip, onClick }) => (
        <Tooltip key={label}>
          <TooltipTrigger asChild>
            <Button
              size={secondarySize}
              variant="outline"
              className={secondaryClass}
              onClick={() => void onClick()}
              aria-label={label}
            >
              <Icon className="h-4 w-4" />
              {!compact && label}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{tooltip}</TooltipContent>
        </Tooltip>
      ))}
    </div>
  );
}
