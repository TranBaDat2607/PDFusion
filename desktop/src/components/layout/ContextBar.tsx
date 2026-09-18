import { ArrowRight, FilePlus, Loader2, Lock, MessageSquare, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageRangeInput } from "@/components/translation/PageRangeInput";
import { TokenEstimate } from "@/components/translation/TokenEstimate";
import { TranslatedFileActions } from "@/components/translation/TranslatedFileActions";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useChatEnabled,
  useConfig,
  useOptions,
  useUpdateConfig,
} from "@/hooks/useConfig";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import { basename } from "@/lib/export-pdf";
import { parsePageRanges } from "@/lib/page-range";
import {
  effectiveService,
  isPairSupported,
  isSourceSupported,
} from "@/lib/translate-request";
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

const ARGOS_ONLY =
  "Offline Argos translates English → Vietnamese only. Add an API key in " +
  "Settings to translate other languages.";

/** Under a language list with locked entries. A disabled Radix item sets
 *  `pointer-events: none`, so a hover tooltip on the row could never fire; the
 *  explanation goes here instead, where it's visible without hovering. */
function LockedNote() {
  return (
    <p className="mt-1 border-t border-border px-2 pt-2 text-[11px] leading-snug text-muted-foreground">
      {ARGOS_ONLY}
    </p>
  );
}

function LanguageLabel({ label, locked }: { label: string; locked: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      {label}
      {locked && (
        <>
          <Lock className="h-3 w-3" />
          <span className="text-[10px] text-muted-foreground">API key needed</span>
        </>
      )}
    </span>
  );
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
  const pageRangeText = useAppStore((s) => s.pageRangeText);
  const pageCount = useAppStore((s) => s.originalPageCount);
  const chatEnabled = useChatEnabled();
  const chatOpen = useAppStore((s) => s.chatOpen);
  const toggleChat = useAppStore((s) => s.toggleChat);

  const sourceLang = config?.translation.default_source_lang ?? "auto";
  const targetLang = config?.translation.default_target_lang ?? "vi";
  const service = config?.translation.preferred_service ?? "openai";
  const activeService = options?.services.find((s) => s.code === service);
  const activeModel = activeService
    ? config?.[service].model ?? activeService.models[0]
    : "";

  // Which languages the backend that will *actually* run can handle. An LLM
  // with no API key is silently downgraded to Argos by the sidecar, so this
  // asks about the effective service — offering Japanese under a keyless
  // "OpenAI" selection would produce a job the sidecar refuses.
  const running = config ? effectiveService(config) : null;
  const sourceSupported = (code: string) =>
    !options || !running ? true : isSourceSupported(options, running, code);
  const targetSupported = (code: string) =>
    !options || !running
      ? true
      : isPairSupported(options, running, sourceLang, code);

  const hasLockedSource = !!options?.languages.some((l) => !sourceSupported(l.code));
  const hasLockedTarget = !!options?.languages.some(
    (l) => l.code !== "auto" && !targetSupported(l.code),
  );

  const ready = config && options;
  // A Pages box the toolbar can't read keeps Translate off; its tooltip says why.
  const pagesValid = parsePageRanges(pageRangeText, pageCount).ok;
  // Switching to Argos can leave a pair it can't do selected. Translate says
  // so here rather than as a refusal after the click (#33).
  const pairSupported = targetSupported(targetLang);
  const canTranslate =
    !!originalPath && !translating && ready && pagesValid && pairSupported;

  const reTranslateButton = (
    <Button
      onClick={onReTranslate}
      disabled={!pagesValid || !pairSupported}
      variant="outline"
      size="sm"
      className="gap-2"
    >
      <RefreshCw className="h-4 w-4" />
      Re-translate
    </Button>
  );

  const translateButton = (
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
  );

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-background px-4 py-2.5">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant={originalPath ? "outline" : "default"}
            size="sm"
            onClick={onPickFile}
            className="gap-2"
          >
            <FilePlus className="h-4 w-4" />
            {originalPath ? "Change PDF" : "Open PDF"}
          </Button>
        </TooltipTrigger>
        <TooltipContent>Ctrl+O, or drop a PDF onto the window</TooltipContent>
      </Tooltip>

      {originalPath && (
        <>
          <div className="flex max-w-[260px] items-center gap-1.5 rounded-md bg-muted px-2.5 py-1 text-xs">
            <span className="truncate font-medium">{basename(originalPath)}</span>
          </div>
          <PageRangeInput
            disabled={translating}
            onSubmit={() => {
              if (canTranslate) onTranslate();
            }}
          />
        </>
      )}

      <div className="mx-1 h-5 w-px bg-border" />

      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground">From</span>
        <Select
          value={sourceLang}
          onValueChange={(v) => {
            // `l.code` is `options.languages[].code: string` — a plain
            // string in the schema, since GET /config/options is generic
            // dropdown data. Every value actually comes from `LanguageCode`
            // on the backend (`routes/config.py:get_options`), so this is a
            // real "wider type, narrower runtime guarantee" boundary cast,
            // not an escape from the same drift this issue closes elsewhere.
            update.mutate({
              default_source_lang: v as components["schemas"]["LanguageCode"],
            });
            prewarm({ source_lang: v, target_lang: targetLang, service });
          }}
        >
          <SelectTrigger size="sm" className="h-8 min-w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options?.languages.map((l) => {
              const supported = sourceSupported(l.code);
              return (
                <SelectItem key={l.code} value={l.code} disabled={!supported}>
                  <LanguageLabel label={l.label} locked={!supported} />
                </SelectItem>
              );
            })}
            {hasLockedSource && <LockedNote />}
          </SelectContent>
        </Select>
        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
        <Select
          value={targetLang}
          onValueChange={(v) => {
            update.mutate({
              default_target_lang: v as components["schemas"]["LanguageCode"],
            });
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
                    <LanguageLabel label={l.label} locked={!supported} />
                  </SelectItem>
                );
              })}
            {hasLockedTarget && <LockedNote />}
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

      {/* What an LLM run would cost, roughly. Argos costs nothing to call. */}
      {originalPath && running && running !== "argos" && (
        <TokenEstimate filePath={originalPath} targetLang={targetLang} />
      )}

      <div className="ml-auto flex items-center gap-2">
        {/* Shows or hides the panel, and does nothing else: Settings → Chat
            turns chat off. This used to be one switch that also turned RAG
            on and off and wrote the config (#32). */}
        {chatEnabled && (
          <>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  aria-pressed={chatOpen}
                  onClick={toggleChat}
                  className={cn(
                    "gap-2",
                    chatOpen &&
                      "border-primary/50 bg-primary/5 text-primary hover:text-primary",
                  )}
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  Chat
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {chatOpen ? "Hide the chat panel" : "Ask questions about the document"}
              </TooltipContent>
            </Tooltip>

            <div className="mx-1 h-5 w-px bg-border" />
          </>
        )}

        {pairSupported || translating ? (
          translateButton
        ) : (
          // A disabled button gets no pointer events, so the tooltip hangs
          // off a wrapper that does.
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="inline-flex">
                {translateButton}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{ARGOS_ONLY}</TooltipContent>
          </Tooltip>
        )}

        {canReTranslate && !translating && (
          <Tooltip>
            <TooltipTrigger asChild>
              {pairSupported ? (
                reTranslateButton
              ) : (
                // Disabled for the same reason Translate is, and disabled
                // buttons get no pointer events — so it needs the same
                // wrapper, or it greys out with nothing to explain it.
                <span tabIndex={0} className="inline-flex">
                  {reTranslateButton}
                </span>
              )}
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              {pairSupported
                ? "Run translation again, bypassing the cached result"
                : ARGOS_ONLY}
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
