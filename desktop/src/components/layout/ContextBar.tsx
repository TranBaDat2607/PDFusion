import { ArrowRight, FilePlus, Loader2, MessageSquare, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useConfig, useOptions, useUpdateConfig } from "@/hooks/useConfig";
import { useAppStore } from "@/lib/store";

interface ContextBarProps {
  onPickFile: () => void;
  onTranslate: () => void;
  translating: boolean;
}

function basename(path: string | null): string {
  if (!path) return "";
  const sep = path.includes("\\") ? "\\" : "/";
  return path.split(sep).pop() ?? path;
}

export function ContextBar({
  onPickFile,
  onTranslate,
  translating,
}: ContextBarProps) {
  const { data: config } = useConfig();
  const { data: options } = useOptions();
  const update = useUpdateConfig();

  const originalPath = useAppStore((s) => s.originalPdfPath);
  const ragEnabled = useAppStore((s) => s.ragEnabled);
  const setRagEnabled = useAppStore((s) => s.setRagEnabled);
  const chatOpen = useAppStore((s) => s.chatOpen);
  const setChatOpen = useAppStore((s) => s.setChatOpen);

  const sourceLang = config?.translation.default_source_lang ?? "auto";
  const targetLang = config?.translation.default_target_lang ?? "vi";
  const service = config?.translation.preferred_service ?? "openai";
  const activeService = options?.services.find((s) => s.code === service);
  const activeModel = activeService
    ? config?.[service].model ?? activeService.models[0]
    : "";

  const ready = config && options;
  const canTranslate = !!originalPath && !translating && ready;

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-background px-4 py-2.5">
      <Button
        variant={originalPath ? "outline" : "default"}
        size="sm"
        onClick={onPickFile}
        className="gap-2"
      >
        <FilePlus className="h-4 w-4" />
        {originalPath ? "Change PDF" : "Open PDF"}
      </Button>

      {originalPath && (
        <div className="flex max-w-[260px] items-center gap-1.5 rounded-md bg-muted px-2.5 py-1 text-xs">
          <span className="truncate font-medium">{basename(originalPath)}</span>
        </div>
      )}

      <div className="mx-1 h-5 w-px bg-border" />

      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground">From</span>
        <Select
          value={sourceLang}
          onValueChange={(v) => update.mutate({ default_source_lang: v })}
        >
          <SelectTrigger size="sm" className="h-8 min-w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options?.languages.map((l) => (
              <SelectItem key={l.code} value={l.code}>
                {l.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
        <Select
          value={targetLang}
          onValueChange={(v) => update.mutate({ default_target_lang: v })}
        >
          <SelectTrigger size="sm" className="h-8 min-w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options?.languages
              .filter((l) => l.code !== "auto")
              .map((l) => (
                <SelectItem key={l.code} value={l.code}>
                  {l.label}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>

      <div className="mx-1 h-5 w-px bg-border" />

      <Select
        value={service}
        onValueChange={(v) =>
          update.mutate({ preferred_service: v as typeof service })
        }
      >
        <SelectTrigger size="sm" className="h-8 min-w-[150px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options?.services.map((s) => (
            <SelectItem key={s.code} value={s.code}>
              {s.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {activeModel && (
        <span className="rounded-md bg-muted px-2 py-1 font-mono text-[10px] text-muted-foreground">
          {activeModel}
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1">
              <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs">Chat</span>
              <Switch
                checked={ragEnabled && chatOpen}
                onCheckedChange={(checked) => {
                  setRagEnabled(checked);
                  setChatOpen(checked);
                  if (checked !== ragEnabled) {
                    update.mutate({ rag_enabled: checked });
                  }
                }}
              />
            </div>
          </TooltipTrigger>
          <TooltipContent>Enable AI chat about the document</TooltipContent>
        </Tooltip>

        <Button
          onClick={onTranslate}
          disabled={!canTranslate}
          size="sm"
          className="gap-2"
        >
          {translating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          {translating ? "Translating…" : "Translate"}
        </Button>
      </div>
    </div>
  );
}
