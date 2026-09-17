import { useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface ModelComboboxProps {
  id: string;
  value: string;
  suggestions: string[];
  onChange: (value: string) => void;
}

/**
 * A model name, typed freely or picked from the service's suggestions (#32).
 *
 * Free text is the point. A local server's models are in no list, and a saved
 * model missing from the list used to leave the old Select blank.
 */
export function ModelCombobox({
  id,
  value,
  suggestions,
  onChange,
}: ModelComboboxProps) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="flex gap-2">
          <Input
            id={id}
            value={value}
            placeholder="Model name"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={!value.trim() || undefined}
            className="font-mono"
            onChange={(e) => onChange(e.target.value)}
          />
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="shrink-0"
              aria-label="Suggested models"
            >
              <ChevronsUpDown className="h-4 w-4" />
            </Button>
          </PopoverTrigger>
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className="w-(--radix-popover-trigger-width) p-0"
      >
        <Command>
          <CommandInput placeholder="Search suggestions…" />
          <CommandList>
            <CommandEmpty>No suggestion matches.</CommandEmpty>
            <CommandGroup heading="Suggested">
              {suggestions.map((model) => (
                <CommandItem
                  key={model}
                  value={model}
                  className="font-mono"
                  onSelect={() => {
                    onChange(model);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "h-4 w-4",
                      model === value.trim() ? "opacity-100" : "opacity-0",
                    )}
                  />
                  {model}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
