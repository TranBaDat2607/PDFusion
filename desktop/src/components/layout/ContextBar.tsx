import { ArrowRight, FilePlus, Loader2, Lock, MessageSquare, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { TranslatedFileActions } from "@/components/translation/TranslatedFileActions";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useConfig, useOptions, useUpdateConfig } from "@/hooks/useConfig";
import { api } from "@/lib/api-client";
import { basename } from "@/lib/export-pdf";
import { effectiveService, isPairSupported } from "@/lib/translate-request";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/** Warm the backend the user just selected. The value is passed in rather than
 *  read back from config: `update.mutate` hasn't round-tripped yet, so config
 *  still holds the *previous* selection at this point. */
function prewarm(selection: {
  source_lang?: string;
  target_lang?: string;
  service?: string;
}) {
  void api.post("/translate/prewarm", selection).catch(() => undefined);
}

interface ContextBarProps {
  onPickFile: () => void;
  onTranslate: () => void;
  onReTranslate: () => void;
  /** A backend worker is still touching the artifact — running *or* draining
   *  a cancel. */
  translating: boolean;
  /** True when a translation has completed for the current document, so the
   *  user can re-run it with the PDF-level cache bypassed. */
  canReTranslate: boolean;
}

export function ContextBar({
  onPickFile,
  onTranslate,
  onReTranslate,
  translating,
  canReTranslate,
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

  // Which targets the backend that will *actually* run can reach. An LLM with
  // no API key is silently downgraded to Argos by the sidecar, so this asks
  // about the effective service — offering Japanese under a keyless "OpenAI"
  // selection would produce a job the sidecar refuses.
  const targetSupported = (code: string) =>
    !config || !options
      ? true
      : isPairSupported(options, effectiveService(config), sourceLang, code);

  const hasLockedTarget = !!options?.languages.some(
    (l) => l.code !== "auto" && !targetSupported(l.code),
  );

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
          onValueChange={(v) => {
            update.mutate({ default_source_lang: v });
            prewarm({ source_lang: v, target_lang: targetLang, service });
          }}
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
          onValueChange={(v) => {
            update.mutate({ default_target_lang: v });
            prewarm({ source_lang: sourceLang, target_lang: v, service });
          }}
        >
          <SelectTrigger size="sm" className="h-8 min-w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options?.languages
              .filter((l) => l.code !== "auto")
              .map((l) => {
                const supported = targetSupported(l.code);
                return (
                  <SelectItem key={l.code} value={l.code} disabled={!supported}>
                    <span className="flex items-center gap-1.5">
                      {l.label}
                      {!supported && (
                        <>
                          <Lock className="h-3 w-3" />
                          <span className="text-[10px] text-muted-foreground">
                            API key needed
                          </span>
                        </>
                      )}
                    </span>
                  </SelectItem>
                );
              })}
            {hasLockedTarget && (
              // A disabled Radix item sets `pointer-events: none`, so a hover
              // tooltip on the row can never fire. The explanation goes here
              // instead, where it's visible without hovering anything.
              <p className="mt-1 border-t border-border px-2 pt-2 text-[11px] leading-snug text-muted-foreground">
                Offline Argos translates English → Vietnamese only. Add an API
                key in Settings to translate to other languages.
              </p>
            )}
          </SelectContent>
        </Select>
      </div>

      <div className="mx-1 h-5 w-px bg-border" />

      <Select
        value={service}
        onValueChange={(v) => {
          update.mutate({ preferred_service: v as typeof service });
          prewarm({ source_lang: sourceLang, target_lang: targetLang, service: v });
        }}
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
            <div
              className={cn(
                "flex items-center gap-2 rounded-md border px-2.5 py-1 transition-colors",
                ragEnabled && chatOpen
                  ? "border-primary/50 bg-primary/5"
                  : "border-border",
              )}
            >
              <MessageSquare
                className={cn(
                  "h-3.5 w-3.5 transition-colors",
                  ragEnabled && chatOpen
                    ? "text-primary"
                    : "text-muted-foreground",
                )}
              />
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

        <div className="mx-1 h-5 w-px bg-border" />

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

        {canReTranslate && !translating && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                onClick={onReTranslate}
                variant="outline"
                size="sm"
                className="gap-2"
              >
                <RefreshCw className="h-4 w-4" />
                Re-translate
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              Run translation again, bypassing the cached result
            </TooltipContent>
          </Tooltip>
        )}

        {/* Save / Open / Reveal live here as well as in the completion
            overlay: the overlay is dismissible, and the translation is still
            sitting in a temp dir that the next run will delete. */}
        {!translating && <TranslatedFileActions compact />}
      </div>
    </div>
  );
}
