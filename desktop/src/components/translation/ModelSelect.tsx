import type { ReactNode } from "react";
import { Check, KeyRound, Settings2 } from "lucide-react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { Popover, PopoverContent } from "@/components/ui/popover";
import type { ModelGroup } from "@/lib/model-choice";
import { cn } from "@/lib/utils";

interface ModelSelectProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The button, wrapped in a `PopoverTrigger`. */
  trigger: ReactNode;
  groups: ModelGroup[];
  isCurrent: (provider: string, model: string) => boolean;
  onPick: (provider: string, model: string) => void;
  /** Settings → Models at this provider's card. */
  onOpenSettings: (provider: string) => void;
  /** The provider "More models, keys and endpoints…" opens Settings at. */
  settingsTarget: string;
  /** Entries above the provider groups: chat's "Same as translation". */
  leading?: ReactNode;
  /** A line under the list. */
  note?: ReactNode;
}

/**
 * The model list both pickers share (#87): search, one group per provider in
 * registry order, the models switched on in Settings → Models, and for a
 * provider with no key an entry that opens its card instead.
 */
export function ModelSelect({
  open,
  onOpenChange,
  trigger,
  groups,
  isCurrent,
  onPick,
  onOpenSettings,
  settingsTarget,
  leading,
  note,
}: ModelSelectProps) {
  const pick = (provider: string, model: string) => {
    onPick(provider, model);
    onOpenChange(false);
  };
  const toSettings = (provider: string) => {
    onOpenChange(false);
    onOpenSettings(provider);
  };

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      {trigger}
      <PopoverContent align="start" className="w-[340px] p-0">
        <Command filter={matchesEveryWord}>
          <CommandInput placeholder="Search models…" />
          <CommandList className="max-h-[420px]">
            <CommandEmpty>No model matches.</CommandEmpty>
            {leading}
            {groups.map((group) => (
              <CommandGroup key={group.id} heading={<GroupHeading group={group} />}>
                {group.needsKey ? (
                  <CommandItem
                    value={`${group.id} ${group.label} add api key`}
                    onSelect={() => toSettings(group.id)}
                  >
                    <KeyRound className="text-muted-foreground" />
                    <span className="text-muted-foreground">
                      Add an API key to use {group.label}…
                    </span>
                  </CommandItem>
                ) : group.fixed ? (
                  group.models.map(({ model }) => (
                    <CommandItem
                      key={model}
                      value={`${group.id} ${group.label} offline`}
                      onSelect={() => pick(group.id, model)}
                    >
                      <CurrentMark on={isCurrent(group.id, model)} />
                      <span className="flex-1">Offline, on this computer</span>
                      <span className="text-[10px] text-muted-foreground">free</span>
                    </CommandItem>
                  ))
                ) : (
                  group.models.map(({ model, isDefault }) => (
                    <CommandItem
                      key={model}
                      value={`${group.id} ${model}`}
                      keywords={[group.label]}
                      onSelect={() => pick(group.id, model)}
                    >
                      <CurrentMark on={isCurrent(group.id, model)} />
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
                value="more models keys endpoints settings"
                onSelect={() => toSettings(settingsTarget)}
              >
                <Settings2 className="text-muted-foreground" />
                More models, keys and endpoints…
              </CommandItem>
            </CommandGroup>
          </CommandList>
          {note && (
            <p className="border-t border-border px-3 py-2 text-[11px] leading-snug text-muted-foreground">
              {note}
            </p>
          )}
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

export function CurrentMark({ on }: { on: boolean }) {
  return <Check className={cn("text-primary", on ? "opacity-100" : "opacity-0")} />;
}

function GroupHeading({ group }: { group: ModelGroup }) {
  const status = group.needsKey
    ? "no key"
    : group.endpoint
      ? hostOf(group.endpoint)
      : group.fixed || group.keyless
        ? "no key needed"
        : "ready";
  return (
    <div className="flex items-center justify-between gap-2">
      <span>{group.label}</span>
      <span
        className={cn(
          "font-normal",
          group.usable ? "text-primary" : "text-muted-foreground",
        )}
      >
        {status}
      </span>
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
