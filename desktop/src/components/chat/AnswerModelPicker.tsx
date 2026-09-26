import { useState } from "react";
import { ChevronsUpDown } from "lucide-react";
import { toast } from "sonner";

import { CurrentMark, ModelSelect } from "@/components/translation/ModelSelect";
import { Button } from "@/components/ui/button";
import { CommandGroup, CommandItem } from "@/components/ui/command";
import { PopoverTrigger } from "@/components/ui/popover";
import { useUpdateConfig, type ConfigResponse } from "@/hooks/useConfig";
import type { ProviderInfo } from "@/hooks/useProviders";
import {
  answerModelUpdate,
  chatModel,
  isCurrentAnswer,
  modelGroups,
  settingsTargetFor,
  type AnswerChoice,
} from "@/lib/model-choice";
import { cn } from "@/lib/utils";

interface AnswerModelPickerProps {
  config: ConfigResponse;
  providers: ProviderInfo[];
  onOpenSettings?: (provider: string) => void;
}

/**
 * The model that writes chat answers, picked in the chat header (#87). It
 * used to be a read-only badge: changing it meant changing the translation
 * model, and chat could still answer with another provider.
 *
 * The choice is `rag.answer_model`, server state saved with `PUT /config`.
 * The sidecar reads it afresh for every question, so it applies from the next
 * one, and a question being answered keeps the model it started with; the
 * picker stays usable meanwhile.
 */
export function AnswerModelPicker({ config, providers, onOpenSettings }: AnswerModelPickerProps) {
  const [open, setOpen] = useState(false);
  const update = useUpdateConfig();
  const answering = chatModel(config, providers);
  const translating = config.translation.model;

  const choose = (choice: AnswerChoice) => {
    const body = answerModelUpdate(config, choice);
    if (Object.keys(body).length === 0) return;
    update.mutate(body, {
      onError: (e) =>
        toast.error("Could not change the chat model", {
          description: (e as Error).message,
        }),
    });
  };

  // The offline engine translates but can't write an answer.
  const groups = modelGroups(config, providers).filter((group) => !group.fixed);

  return (
    <ModelSelect
      open={open}
      onOpenChange={setOpen}
      trigger={
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            role="combobox"
            aria-expanded={open}
            aria-label="Chat model"
            title={
              answering
                ? `${answering.label} writes the answers.`
                : "No provider has a key, so answers are excerpts from the document."
            }
            className={cn(
              "h-6 max-w-[190px] gap-1 px-2 font-mono text-[10px] font-normal",
              !answering && "border-amber-500/60 text-amber-600 dark:text-amber-400",
            )}
          >
            <span className="truncate">{answering ? answering.model : "no model"}</span>
            <ChevronsUpDown className="h-3 w-3 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
      }
      leading={
        <CommandGroup heading="Chat answers with">
          <CommandItem
            value="same as translation follow"
            onSelect={() => {
              choose(null);
              setOpen(false);
            }}
          >
            <CurrentMark on={isCurrentAnswer(config, null)} />
            <span className="flex-1">Same as translation</span>
            <span className="truncate font-mono text-[10px] text-muted-foreground">
              {translating.model}
            </span>
          </CommandItem>
        </CommandGroup>
      }
      groups={groups}
      isCurrent={(provider, model) => isCurrentAnswer(config, { provider, model })}
      onPick={(provider, model) => choose({ provider, model })}
      onOpenSettings={(provider) => onOpenSettings?.(provider)}
      settingsTarget={settingsTargetFor(config, providers)}
      note="Applies from the next question. A provider with no key is skipped, and the next one that has a key answers."
    />
  );
}
