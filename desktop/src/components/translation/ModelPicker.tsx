import { useState } from "react";
import { AlertTriangle, ChevronsUpDown, ShieldCheck } from "lucide-react";

import { ModelSelect } from "@/components/translation/ModelSelect";
import { Button } from "@/components/ui/button";
import { PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ConfigResponse } from "@/hooks/useConfig";
import type { ProviderInfo } from "@/hooks/useProviders";
import {
  isCurrent,
  modelGroups,
  pickerSummary,
  settingsTargetFor,
} from "@/lib/model-choice";
import { cn } from "@/lib/utils";

interface ModelPickerProps {
  config: ConfigResponse;
  providers: ProviderInfo[];
  onSelect: (provider: string, model: string) => void;
  /** Settings → Models, at this provider's card. */
  onOpenSettings: (provider: string) => void;
}

/**
 * The translation model: the provider and its model, picked in one place. It
 * offers the models switched on in Settings → Models (#86), so opening it asks
 * no server anything; a name of one's own is added there.
 */
export function ModelPicker({
  config,
  providers,
  onSelect,
  onOpenSettings,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const summary = pickerSummary(config, providers);

  const trigger = (
    <PopoverTrigger asChild>
      <Button
        variant="outline"
        size="sm"
        role="combobox"
        aria-expanded={open}
        aria-label="Translation model"
        className={cn(
          "h-8 max-w-[300px] gap-2 font-normal",
          summary.downgradedFrom && "border-amber-500/60",
        )}
      >
        {summary.downgradedFrom ? (
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
        ) : summary.model === null ? (
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-primary" />
        ) : null}
        <span className="font-medium">{summary.service}</span>
        {summary.model ? (
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            {summary.model}
          </span>
        ) : (
          <span className="text-[11px] text-muted-foreground">offline</span>
        )}
        <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
      </Button>
    </PopoverTrigger>
  );

  return (
    <ModelSelect
      open={open}
      onOpenChange={setOpen}
      trigger={
        summary.downgradedFrom ? (
          <Tooltip>
            <TooltipTrigger asChild>{trigger}</TooltipTrigger>
            <TooltipContent className="max-w-xs">
              {summary.downgradedFrom} has no API key, so {summary.service} translates
              offline instead. Pick it to add a key.
            </TooltipContent>
          </Tooltip>
        ) : (
          trigger
        )
      }
      groups={modelGroups(config, providers)}
      isCurrent={(provider, model) => isCurrent(config, provider, model)}
      onPick={onSelect}
      onOpenSettings={onOpenSettings}
      settingsTarget={settingsTargetFor(config, providers)}
      note="Translations are cached per model, so a document done with one model is translated again, and billed again, with another."
    />
  );
}
