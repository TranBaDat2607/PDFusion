import { Info, Languages } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useConfig, useUpdateConfig } from "@/hooks/useConfig";
import { useProviders } from "@/hooks/useProviders";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import {
  MAX_INSTRUCTIONS_CHARS,
  instructionsDraft,
  translatesWithLlm,
} from "@/lib/translation-instructions";

/**
 * Settings → Translation: instructions added to every paragraph's prompt.
 * Saved with a button rather than as the user types — each `PUT /config`
 * rewrites `config.toml`, and a half-typed instruction is not one to run with.
 */
export function TranslationTab() {
  const { data: config } = useConfig();
  const { data: providers } = useProviders();
  const update = useUpdateConfig();

  // `null` until edited, so the box shows whatever is saved — including a
  // value saved elsewhere while the sheet was closed — without an effect to
  // copy it in.
  const edited = useAppStore((s) => s.instructionsDraft);
  const setEdited = useAppStore((s) => s.setInstructionsDraft);
  const saved = config?.translation.instructions ?? null;
  const text = edited ?? saved ?? "";
  const draft = instructionsDraft(text, saved);
  const llm = config && providers ? translatesWithLlm(config, providers) : true;

  const save = (value: string | null) =>
    update.mutate(
      { translation_instructions: value },
      {
        onSuccess: () => {
          // Safe to drop: the box is locked while a save is in flight, so
          // nothing was typed after what was sent.
          setEdited(null);
          toast.success(value ? "Instructions saved" : "Instructions cleared");
        },
        onError: (e) =>
          toast.error("Could not save the instructions", {
            description: (e as Error).message,
          }),
      },
    );

  return (
    <div className="space-y-4 py-2">
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <Languages className="h-4 w-4 text-primary" />
          <Label htmlFor="translation-instructions" className="text-sm font-medium">
            Translation instructions
          </Label>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Tell the model how to translate: tone, terms to keep, how to render
          names. Sent with every paragraph, so a long instruction costs tokens
          on each one. Applies to translation only, not to chat.
        </p>
        <Textarea
          id="translation-instructions"
          value={text}
          disabled={!config || update.isPending}
          placeholder={
            "e.g. Keep programming terms such as index, array and pointer in English."
          }
          aria-invalid={draft.tooLong}
          onChange={(e) => setEdited(e.target.value)}
          className="max-h-72 min-h-32"
        />
        <div className="flex items-center justify-between gap-3">
          <span
            className={cn(
              "text-xs tabular-nums text-muted-foreground",
              draft.tooLong && "text-destructive",
            )}
          >
            {text.trim().length} / {MAX_INSTRUCTIONS_CHARS}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!saved || update.isPending}
              onClick={() => save(null)}
            >
              Clear
            </Button>
            <Button
              size="sm"
              disabled={!draft.dirty || draft.tooLong || update.isPending}
              onClick={() => save(draft.value)}
            >
              Save
            </Button>
          </div>
        </div>
        {!llm && (
          <p className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Instructions are sent only to an LLM with a key. Until one
            translates, Argos does, and it takes none.
          </p>
        )}
      </section>
    </div>
  );
}
