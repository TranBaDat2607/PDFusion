import { useState } from "react";
import { useQueries } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronsUpDown,
  KeyRound,
  Loader2,
  Settings2,
  ShieldCheck,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ConfigResponse, OptionsResponse, ServiceCode } from "@/hooks/useConfig";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import {
  isCurrent,
  modelGroups,
  pickerSummary,
  servicesToList,
  settingsTabFor,
  type ServiceGroup,
} from "@/lib/model-choice";
import type { LlmServiceCode } from "@/lib/service-settings";
import { cn } from "@/lib/utils";

type EndpointModels = components["schemas"]["EndpointModelsResponse"];

interface ModelPickerProps {
  config: ConfigResponse;
  options: OptionsResponse;
  onSelect: (service: ServiceCode, model: string | null) => void;
  /** Settings, on this service's tab. */
  onOpenSettings: (service: LlmServiceCode) => void;
}

/**
 * The service and its model, picked in one place. Picking a
 * suggestion saves without the provider check Settings runs: a listed name
 * can't be mistyped. A name of one's own goes through Settings, which checks
 * it.
 */
export function ModelPicker({
  config,
  options,
  onSelect,
  onOpenSettings,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const summary = pickerSummary(config);

  // Only once opened: each is a round-trip to a server that may not be up.
  const listed = servicesToList(config);
  const lists = useQueries({
    queries: listed.map((code) => ({
      queryKey: ["config", "endpoint-models", code, config[code].base_url],
      queryFn: () => api.get<EndpointModels>(`/config/models/${code}`),
      enabled: open,
      staleTime: 60_000,
    })),
  });
  const endpointModels: Partial<Record<LlmServiceCode, string[]>> = {};
  const endpointState: Partial<
    Record<LlmServiceCode, { loading: boolean; error: string | null }>
  > = {};
  listed.forEach((code, i) => {
    const query = lists[i];
    endpointModels[code] = query.data?.models ?? [];
    endpointState[code] = {
      loading: query.isFetching && !query.data,
      error: query.error?.message ?? query.data?.error ?? null,
    };
  });

  const groups = modelGroups(config, options, endpointModels);
  const pick = (code: ServiceCode, model: string | null) => {
    onSelect(code, model);
    setOpen(false);
  };
  const toSettings = (code: LlmServiceCode) => {
    setOpen(false);
    onOpenSettings(code);
  };

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
    <Popover open={open} onOpenChange={setOpen}>
      {summary.downgradedFrom ? (
        <Tooltip>
          <TooltipTrigger asChild>{trigger}</TooltipTrigger>
          <TooltipContent className="max-w-xs">
            {summary.downgradedFrom} has no API key, so Argos translates offline
            instead (English → Vietnamese only). Pick a model to add a key.
          </TooltipContent>
        </Tooltip>
      ) : (
        trigger
      )}
      <PopoverContent align="start" className="w-[340px] p-0">
        <Command filter={matchesEveryWord}>
          <CommandInput placeholder="Search models…" />
          <CommandList className="max-h-[420px]">
            <CommandEmpty>No model matches.</CommandEmpty>
            {groups.map((group) => (
              <CommandGroup
                key={group.code}
                heading={
                  <GroupHeading
                    group={group}
                    listing={endpointState[group.code as LlmServiceCode]}
                  />
                }
              >
                {group.code === "argos" ? (
                  <CommandItem
                    value="argos offline argos translate"
                    onSelect={() => pick("argos", null)}
                  >
                    <CurrentMark on={isCurrent(config, "argos", null)} />
                    <span className="flex-1">Argos Translate</span>
                    <span className="text-[10px] text-muted-foreground">
                      free · EN → VI only
                    </span>
                  </CommandItem>
                ) : !group.hasKey ? (
                  <CommandItem
                    value={`${group.code} ${group.label} add api key`}
                    onSelect={() => toSettings(group.code as LlmServiceCode)}
                  >
                    <KeyRound className="text-muted-foreground" />
                    <span className="text-muted-foreground">
                      Add an API key to use {group.label}…
                    </span>
                  </CommandItem>
                ) : (
                  group.models.map(({ model, isDefault }) => (
                    <CommandItem
                      key={model}
                      value={`${group.code} ${model}`}
                      keywords={[group.label]}
                      onSelect={() => pick(group.code, model)}
                    >
                      <CurrentMark on={isCurrent(config, group.code, model)} />
                      <span className="flex-1 truncate font-mono text-xs">{model}</span>
                      {isDefault && (
                        <span className="text-[10px] text-muted-foreground">default</span>
                      )}
                    </CommandItem>
                  ))
                )}
              </CommandGroup>
            ))}
            <CommandSeparator />
            <CommandGroup>
              <CommandItem
                value="custom model endpoint settings"
                onSelect={() => toSettings(settingsTabFor(config))}
              >
                <Settings2 className="text-muted-foreground" />
                Custom model or endpoint…
              </CommandItem>
            </CommandGroup>
          </CommandList>
          <p className="border-t border-border px-3 py-2 text-[11px] leading-snug text-muted-foreground">
            Translations are cached per model, so a document done with one model
            is translated again, and billed again, with another.
          </p>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/** cmdk's default match is fuzzy, which for model names is noise: "gemma"
 *  matched "Add an API key to use Google Gemini" letter by letter. */
function matchesEveryWord(value: string, search: string, keywords?: string[]): number {
  const haystack = [value, ...(keywords ?? [])].join(" ").toLowerCase();
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  return words.every((word) => haystack.includes(word)) ? 1 : 0;
}

function CurrentMark({ on }: { on: boolean }) {
  return <Check className={cn("text-primary", on ? "opacity-100" : "opacity-0")} />;
}

function GroupHeading({
  group,
  listing,
}: {
  group: ServiceGroup;
  listing?: { loading: boolean; error: string | null };
}) {
  const status =
    group.code === "argos"
      ? "no key needed"
      : !group.hasKey
        ? "no key"
        : group.endpoint
          ? hostOf(group.endpoint)
          : "key saved";
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span>{group.label}</span>
        <span
          className={cn(
            "flex items-center gap-1 font-normal",
            group.hasKey ? "text-primary" : "text-muted-foreground",
          )}
        >
          {listing?.loading && <Loader2 className="h-3 w-3 animate-spin" />}
          {status}
        </span>
      </div>
      {listing?.error && (
        <p className="mt-0.5 font-normal text-destructive" title={listing.error}>
          Couldn't list this server's models — {truncate(listing.error, 80)}
        </p>
      )}
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}
