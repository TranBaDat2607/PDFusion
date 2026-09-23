import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { ChatIndexTab } from "@/components/settings/ChatIndexTab";
import { ModelCombobox } from "@/components/settings/ModelCombobox";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import {
  clearConfirmation,
  describeCleared,
  formatMegabytes,
  type CacheTarget,
  type ClearScope,
} from "@/lib/cache-settings";
import {
  PAGE_LIMIT_HELP,
  PAGE_LIMIT_PRESETS,
  SIZE_LIMIT_HELP,
  SIZE_LIMIT_PRESETS_MB,
  formatPageLimit,
  formatSizeLimit,
  limitOptions,
  type LimitOption,
} from "@/lib/performance-settings";
import {
  useConfig,
  useOptions,
  useUpdateConfig,
  useValidateCredentials,
  validateCredentials,
  type ServiceCode,
  type ServiceOption,
} from "@/hooks/useConfig";
import {
  LLM_SERVICES,
  buildConfigUpdate,
  draftProblem,
  draftsFrom,
  endpointNeedsKey,
  isValidEndpoint,
  servicesToProbe,
  takesEndpoint,
  validateRequestFor,
  type LlmServiceCode,
  type SavedServices,
  type ServiceDraft,
  type ServiceDrafts,
} from "@/lib/service-settings";
import { SERVICE_SHORT_LABELS } from "@/lib/model-choice";
import { cn } from "@/lib/utils";

const ALL_SERVICES: ServiceCode[] = ["argos", "openai", "gemini", "anthropic"];

// Pseudo-tab values for the cache and chat panels — not translation services.
type TabValue = ServiceCode | "cache" | "chat";

// Where a service with a blank endpoint sends its requests, shown as the
// field's placeholder.
const DEFAULT_ENDPOINTS: Partial<Record<LlmServiceCode, string>> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
};

const ENDPOINT_HINTS: Partial<Record<LlmServiceCode, string>> = {
  openai:
    "Leave blank for OpenAI. For a local model, use Ollama at http://localhost:11434/v1 or LM Studio at http://localhost:1234/v1, with any API key.",
  anthropic:
    "Leave blank for Anthropic. For a local model, use Ollama at http://localhost:11434, with any API key.",
};

type Problems = Partial<Record<LlmServiceCode, string>>;

interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The tab to open on. Unset opens on the service in use. */
  initialTab?: LlmServiceCode;
}

export function SettingsSheet({ open, onOpenChange, initialTab }: SettingsSheetProps) {
  const { data: config } = useConfig();
  const { data: options } = useOptions();
  const updateConfig = useUpdateConfig();

  const [tab, setTab] = useState<TabValue>("argos");
  // Picked on opening only: the effect below also runs when the config
  // changes, which a Cache tab switch does, and must not move the user off
  // the tab they're on. It used to open on Argos whatever was in use.
  const [wasOpen, setWasOpen] = useState(false);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setTab(initialTab ?? config?.translation.preferred_service ?? "argos");
  }
  const [drafts, setDrafts] = useState<ServiceDrafts | null>(null);
  // Why the last Save stopped, per service: a draft the sidecar would refuse,
  // or what the provider said when Save checked it.
  const [problems, setProblems] = useState<Problems>({});
  // The provider turned a draft down. Another Save saves without checking:
  // a local server may just not be running yet.
  const [saveAnyway, setSaveAnyway] = useState(false);
  const [checking, setChecking] = useState(false);

  // Reset drafts whenever the sheet opens (or config changes)
  useEffect(() => {
    if (!open || !config) return;
    setDrafts(draftsFrom(config));
    setProblems({});
    setSaveAnyway(false);
  }, [open, config]);

  const editDraft = (code: LlmServiceCode, patch: Partial<ServiceDraft>) => {
    setDrafts((prev) => prev && { ...prev, [code]: { ...prev[code], ...patch } });
    setProblems((prev) => ({ ...prev, [code]: undefined }));
    setSaveAnyway(false);
  };

  const handleSave = async () => {
    if (!config || !drafts) {
      onOpenChange(false);
      return;
    }

    // What the sidecar would refuse is shown on its own tab instead.
    const refused: Problems = {};
    for (const code of LLM_SERVICES) {
      const problem = draftProblem(code, drafts[code], config);
      if (problem) refused[code] = problem;
    }
    const firstRefused = LLM_SERVICES.find((code) => refused[code]);
    if (firstRefused) {
      setProblems(refused);
      setTab(firstRefused);
      return;
    }

    const update = buildConfigUpdate(drafts, config);
    if (Object.keys(update).length === 0) {
      onOpenChange(false);
      return;
    }

    // A changed key, model or endpoint is checked with the provider before it
    // is saved, so a mistyped model name turns up here and not as a document
    // that fails one paragraph at a time.
    if (!saveAnyway) {
      const probes = servicesToProbe(drafts, config);
      if (probes.length > 0) {
        setChecking(true);
        const results = await Promise.all(
          probes.map(async ({ code, request }) => {
            try {
              return { code, ...(await validateCredentials(request)) };
            } catch (e) {
              return { code, valid: false, message: (e as Error).message };
            }
          }),
        );
        setChecking(false);
        const failed = results.filter((result) => !result.valid);
        if (failed.length > 0) {
          setProblems(
            Object.fromEntries(failed.map((result) => [result.code, result.message])),
          );
          setTab(failed[0].code);
          setSaveAnyway(true);
          return;
        }
      }
    }

    // Opened from the model picker's "Add an API key" for a service that had
    // none: the user came to translate with it. The sidecar only moves off
    // Argos by itself, so from a keyless LLM they would otherwise stay on
    // Argos. Not on "Save anyway", which saves a key the provider turned down.
    if (
      initialTab &&
      !config[initialTab].has_key &&
      update[initialTab]?.api_key &&
      !saveAnyway &&
      config.translation.preferred_service !== initialTab
    ) {
      update.preferred_service = initialTab;
    }

    try {
      await updateConfig.mutateAsync(update);
      toast.success("Settings saved");
      onOpenChange(false);
    } catch (e) {
      toast.error("Failed to save settings", { description: (e as Error).message });
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Settings</SheetTitle>
          <SheetDescription>
            Manage API keys, models and endpoints for each translation service.
            Keys are encrypted at rest.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4">
          <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
            <TabsList className="grid w-full grid-cols-6">
              {ALL_SERVICES.map((c) => (
                <TabsTrigger
                  key={c}
                  value={c}
                  title={options?.services.find((s) => s.code === c)?.label ?? c}
                >
                  {/* The full names ("Argos Translate (offline)") are wider
                      than a column and overlapped their neighbours. */}
                  {SERVICE_SHORT_LABELS[c]}
                </TabsTrigger>
              ))}
              <TabsTrigger value="cache">Cache</TabsTrigger>
              <TabsTrigger value="chat">Chat</TabsTrigger>
            </TabsList>

            <TabsContent value="argos" className="mt-4">
              <ArgosTab />
            </TabsContent>

            <TabsContent value="cache" className="mt-4">
              <CacheTab open={open && tab === "cache"} />
              <div className="mt-6 border-t border-border pt-6">
                <PerformanceSection />
              </div>
            </TabsContent>

            <TabsContent value="chat" className="mt-4">
              <ChatIndexTab open={open && tab === "chat"} />
            </TabsContent>

            {config &&
              drafts &&
              LLM_SERVICES.map((code) => {
                const opt = options?.services.find((s) => s.code === code);
                if (!opt) return null;
                return (
                  <TabsContent key={code} value={code} className="mt-4">
                    <ServiceTab
                      code={code}
                      option={opt}
                      saved={config}
                      draft={drafts[code]}
                      problem={problems[code]}
                      focusKey={code === initialTab && !config[code].has_key}
                      onChange={(patch) => editDraft(code, patch)}
                    />
                  </TabsContent>
                );
              })}
          </Tabs>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={checking || updateConfig.isPending}
          >
            {checking ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Checking…
              </>
            ) : updateConfig.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving…
              </>
            ) : saveAnyway ? (
              "Save anyway"
            ) : (
              "Save"
            )}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function ArgosTab() {
  const validate = useValidateCredentials();
  const [status, setStatus] = useState<{ valid: boolean; message: string } | null>(
    null,
  );

  const handleCheck = async () => {
    try {
      const result = await validate.mutateAsync({
        service: "argos",
        api_key: "",
      });
      setStatus(result);
    } catch (e) {
      setStatus({ valid: false, message: (e as Error).message });
    }
  };

  return (
    <div className="space-y-4 py-2">
      <div className="rounded-md border border-border bg-muted/40 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Offline translation (free)</span>
        </div>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Argos Translate runs entirely on your machine. No API key, no usage
          fees, no data leaves your computer once the engine is installed. Used
          automatically when no LLM key is configured.
        </p>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-md bg-background px-2 py-1 font-mono text-muted-foreground">
            English → Vietnamese
          </span>
          <span className="text-muted-foreground">
            Other source languages need an LLM key.
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          The language pack (~80 MB) is installed by PDFusion's one-time setup
          step, from the copy included with the app where there is one.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={handleCheck}
          disabled={validate.isPending}
          variant="secondary"
        >
          {validate.isPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Checking…
            </>
          ) : (
            "Check status"
          )}
        </Button>
        {status && (
          <span
            className={`flex items-center gap-1 text-sm ${
              status.valid ? "text-primary" : "text-destructive"
            }`}
          >
            {status.valid ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            {status.message}
          </span>
        )}
      </div>
    </div>
  );
}

interface ServiceTabProps {
  code: LlmServiceCode;
  option: ServiceOption;
  saved: SavedServices;
  draft: ServiceDraft;
  /** Why the last Save stopped on this service. */
  problem?: string;
  /** Opened to add this service's key: start in the key field. */
  focusKey?: boolean;
  onChange: (patch: Partial<ServiceDraft>) => void;
}

function ServiceTab({
  code,
  option,
  saved,
  draft,
  problem,
  focusKey,
  onChange,
}: ServiceTabProps) {
  const [show, setShow] = useState(false);
  const validate = useValidateCredentials();
  const [validation, setValidation] = useState<{
    valid: boolean;
    message: string;
  } | null>(null);
  const hasSavedKey = saved[code].has_key;
  const endpointValid = isValidEndpoint(draft.baseUrl);

  const handleValidate = async () => {
    if (!endpointValid) {
      setValidation({
        valid: false,
        message: "The endpoint must be an http:// or https:// URL",
      });
      return;
    }
    if (endpointNeedsKey(code, draft, saved)) {
      setValidation({
        valid: false,
        message: "Enter the API key to check a different endpoint",
      });
      return;
    }
    const request = validateRequestFor(code, draft, saved);
    if (!request) {
      setValidation({ valid: false, message: "Enter an API key first" });
      return;
    }
    try {
      setValidation(await validate.mutateAsync(request));
    } catch (e) {
      setValidation({ valid: false, message: (e as Error).message });
    }
  };

  return (
    <div className="space-y-4 py-2">
      <div className="space-y-2">
        <Label htmlFor={`${code}-key`}>API key</Label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Input
              id={`${code}-key`}
              type={show ? "text" : "password"}
              autoComplete="off"
              autoFocus={focusKey}
              value={draft.apiKey}
              placeholder={
                hasSavedKey && !draft.clearKey ? "•••••••• (saved)" : "Paste key…"
              }
              onChange={(e) =>
                onChange({ apiKey: e.target.value, clearKey: false })
              }
            />
            <button
              type="button"
              onClick={() => setShow((s) => !s)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label={show ? "Hide key" : "Show key"}
            >
              {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <Button
            variant="outline"
            onClick={() => onChange({ apiKey: "", clearKey: true })}
            disabled={!draft.apiKey && (!hasSavedKey || draft.clearKey)}
          >
            Clear
          </Button>
        </div>
        {draft.clearKey ? (
          <p className="text-xs text-muted-foreground">
            The saved key is removed when you save.
          </p>
        ) : (
          hasSavedKey &&
          !draft.apiKey && (
            <p className="text-xs text-muted-foreground">
              A key is currently saved. Type a new one to replace, or click
              Clear and Save to remove.
            </p>
          )
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor={`${code}-model`}>Model</Label>
        <ModelCombobox
          id={`${code}-model`}
          value={draft.model}
          suggestions={option.models}
          onChange={(model) => onChange({ model })}
        />
        <p className="text-xs text-muted-foreground">
          Any model the service offers. The list only makes suggestions.
        </p>
      </div>

      {takesEndpoint(code) && (
        <div className="space-y-2">
          <Label htmlFor={`${code}-endpoint`}>Endpoint</Label>
          <Input
            id={`${code}-endpoint`}
            value={draft.baseUrl}
            placeholder={DEFAULT_ENDPOINTS[code]}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={!endpointValid || undefined}
            className="font-mono"
            onChange={(e) => onChange({ baseUrl: e.target.value })}
          />
          <p className="text-xs text-muted-foreground">{ENDPOINT_HINTS[code]}</p>
        </div>
      )}

      {problem && (
        <p className="flex items-start gap-1.5 text-sm text-destructive">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 break-words">{problem}</span>
        </p>
      )}

      <div className="flex items-start gap-3">
        <Button
          onClick={handleValidate}
          disabled={
            validate.isPending ||
            (!draft.apiKey.trim() && (!hasSavedKey || draft.clearKey))
          }
          variant="secondary"
          className="shrink-0"
        >
          {validate.isPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Validating…
            </>
          ) : (
            "Validate"
          )}
        </Button>
        {validation && (
          <span
            className={`flex min-w-0 items-start gap-1 pt-2 text-sm ${
              validation.valid ? "text-primary" : "text-destructive"
            }`}
          >
            {validation.valid ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span className="min-w-0 break-words">{validation.message}</span>
          </span>
        )}
      </div>
    </div>
  );
}

type CacheOverview = components["schemas"]["CacheOverviewResponse"];
type CacheClearResult = components["schemas"]["CacheClearResponse"];

const CACHE_STATS_KEY = ["cache-stats"] as const;

/**
 * Settings → Cache: the paragraph cache and the translated-PDF cache, each with
 * its own switch, size and Clear (#32). The tab used to show only the
 * paragraph cache, while its "Clear all" emptied both.
 */
function CacheTab({ open }: { open: boolean }) {
  const queryClient = useQueryClient();
  const { data: config } = useConfig();
  const update = useUpdateConfig();
  // Kept after the dialog closes, so its text doesn't change mid-animation.
  const [confirmTarget, setConfirmTarget] = useState<CacheTarget>("paragraph");
  const [confirming, setConfirming] = useState(false);

  const stats = useQuery({
    queryKey: CACHE_STATS_KEY,
    queryFn: () => api.get<CacheOverview>("/config/cache"),
    enabled: open,
  });

  const clear = useMutation({
    mutationFn: ({ target, scope }: { target: CacheTarget; scope: ClearScope }) =>
      api.delete<CacheClearResult>(`/config/cache?scope=${scope}&target=${target}`),
    onSuccess: (result, { target, scope }) => {
      toast.success(describeCleared(result.removed, target, scope));
      void queryClient.invalidateQueries({ queryKey: CACHE_STATS_KEY });
    },
    onError: (e) =>
      toast.error("Could not clear the cache", {
        description: (e as Error).message,
      }),
  });

  const confirmClear = (target: CacheTarget) => {
    setConfirmTarget(target);
    setConfirming(true);
  };

  const paragraph = stats.data?.paragraph;
  const pdf = stats.data?.pdf;
  const confirmation = clearConfirmation(confirmTarget);

  return (
    <div className="space-y-6 py-2">
      {stats.isError && (
        <p className="text-sm text-destructive">
          Could not load the caches: {stats.error.message}
        </p>
      )}

      <CacheSection
        icon={<Database className="h-4 w-4 text-primary" />}
        title="Paragraph cache"
        switchLabel="Cache translated paragraphs"
        enabled={config?.translation.cache_translations ?? true}
        onEnabledChange={
          config
            ? (checked) => update.mutate({ cache_translations: checked })
            : undefined
        }
        description="Every translated paragraph, kept by its text, languages, service and model. A paragraph translated before isn't sent to the translation service again."
        location={paragraph?.cache_dir}
      >
        {paragraph ? (
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Size" value={formatMegabytes(paragraph.size_mb)} />
            <Stat label="Paragraphs" value={String(paragraph.active)} />
            <Stat label="Expired" value={String(paragraph.expired)} />
            <Stat label="Hit rate" value={hitRate(paragraph)} />
            <Stat label="Expire after" value={`${paragraph.ttl_days} days`} />
            <Stat label="Size limit" value={formatMegabytes(paragraph.max_size_mb)} />
          </div>
        ) : (
          <StatsLoading pending={stats.isPending} />
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={clear.isPending || !paragraph?.expired}
            onClick={() => clear.mutate({ target: "paragraph", scope: "expired" })}
          >
            Remove expired
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={clear.isPending || !paragraph?.entries}
            onClick={() => confirmClear("paragraph")}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Clear paragraphs
          </Button>
        </div>
      </CacheSection>

      <CacheSection
        separated
        icon={<FileText className="h-4 w-4 text-primary" />}
        title="Translated PDF cache"
        switchLabel="Cache translated PDFs"
        enabled={config?.translation.cache_translated_pdfs ?? true}
        onEnabledChange={
          config
            ? (checked) => update.mutate({ cache_translated_pdfs: checked })
            : undefined
        }
        description="Every translated PDF, kept by the file's contents, languages, service and model. Translating the same PDF again shows the kept copy in under a second; Re-translate runs the pipeline anyway."
        location={pdf?.cache_dir}
      >
        {pdf ? (
          <>
            <div className="grid grid-cols-3 gap-2">
              <Stat label="Size" value={formatMegabytes(pdf.size_mb)} />
              <Stat label="PDFs" value={String(pdf.entries)} />
              <Stat label="Hit rate" value={hitRate(pdf)} />
            </div>
            <p className="text-xs text-muted-foreground">
              Past {formatMegabytes(pdf.max_size_mb)}, the PDFs used longest ago
              are removed first.
            </p>
          </>
        ) : (
          <StatsLoading pending={stats.isPending} />
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={clear.isPending || !pdf?.entries}
            onClick={() => confirmClear("pdf")}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Clear PDFs
          </Button>
        </div>
      </CacheSection>

      <Button
        variant="secondary"
        size="sm"
        onClick={() => void stats.refetch()}
        disabled={stats.isFetching}
      >
        {stats.isFetching ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <RefreshCw className="mr-2 h-4 w-4" />
        )}
        Refresh
      </Button>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirmation.title}</DialogTitle>
            <DialogDescription>{confirmation.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                clear.mutate({ target: confirmTarget, scope: "all" });
                setConfirming(false);
              }}
            >
              {confirmation.action}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function hitRate(stats: { hits: number; misses: number; hit_rate: number }): string {
  return stats.hits + stats.misses === 0
    ? "—"
    : `${Math.round(stats.hit_rate * 100)}%`;
}

interface CacheSectionProps {
  icon: ReactNode;
  title: string;
  description: string;
  switchLabel: string;
  enabled: boolean;
  /** Absent until the config has loaded, which disables the switch. */
  onEnabledChange?: (enabled: boolean) => void;
  location?: string;
  separated?: boolean;
  children: ReactNode;
}

function CacheSection({
  icon,
  title,
  description,
  switchLabel,
  enabled,
  onEnabledChange,
  location,
  separated,
  children,
}: CacheSectionProps) {
  return (
    <section className={cn("space-y-3", separated && "border-t border-border pt-6")}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-sm font-medium">{title}</span>
        </div>
        <Switch
          aria-label={switchLabel}
          checked={enabled}
          disabled={!onEnabledChange}
          onCheckedChange={onEnabledChange}
        />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
      {children}
      {location && (
        <p className="break-all text-xs text-muted-foreground">
          Stored in <code>{location}</code>
        </p>
      )}
    </section>
  );
}

function StatsLoading({ pending }: { pending: boolean }) {
  return (
    <p className="text-sm text-muted-foreground">
      {pending ? "Loading…" : "No data"}
    </p>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-sm font-medium">{value}</div>
    </div>
  );
}

const PARALLELISM_PRESETS: Array<{
  value: number;
  label: string;
  hint: string;
}> = [
  { value: 0, label: "Auto", hint: "cpu // 2, clamped to 2-8" },
  { value: 1, label: "Quality", hint: "1 chunk at a time — lowest memory" },
  { value: 2, label: "Balanced (low)", hint: "2 chunks in flight" },
  { value: 4, label: "Balanced", hint: "4 chunks (previous default)" },
  { value: 6, label: "Turbo", hint: "6 chunks — needs a powerful machine" },
  { value: 8, label: "Max", hint: "8 chunks — maximum pipeline depth" },
];

function LimitSelect({
  id,
  label,
  help,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  value: number | undefined;
  options: LimitOption[];
  /** Absent until the config has loaded, which disables the Select. */
  onChange?: (value: number) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Select
        value={value === undefined ? undefined : String(value)}
        disabled={!onChange}
        onValueChange={(v) => onChange?.(Number(v))}
      >
        <SelectTrigger id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={String(o.value)}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">{help}</p>
    </div>
  );
}

function PerformanceSection() {
  const { data: config } = useConfig();
  const update = useUpdateConfig();
  const current = config?.processing?.max_parallel_chunks ?? 0;
  const maxPages = config?.translation.max_pages;
  const maxSize = config?.translation.max_file_size_mb;
  const saveLimit = (body: { max_pages?: number; max_file_size_mb?: number }) =>
    update.mutate(body, {
      onError: (e) =>
        toast.error("Could not save the limit", {
          description: (e as Error).message,
        }),
    });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">Performance</span>
      </div>

      <LimitSelect
        id="max-pages"
        label="Pages per translation"
        help={PAGE_LIMIT_HELP}
        value={maxPages}
        options={limitOptions(PAGE_LIMIT_PRESETS, maxPages, formatPageLimit)}
        onChange={config ? (v) => saveLimit({ max_pages: v }) : undefined}
      />

      <LimitSelect
        id="max-file-size"
        label="Largest PDF"
        help={SIZE_LIMIT_HELP}
        value={maxSize}
        options={limitOptions(SIZE_LIMIT_PRESETS_MB, maxSize, formatSizeLimit)}
        onChange={config ? (v) => saveLimit({ max_file_size_mb: v }) : undefined}
      />

      <div className="space-y-2">
        <Label htmlFor="parallel-chunks">Parallel pages</Label>
        <Select
          value={String(current)}
          onValueChange={(v) =>
            update.mutate({ max_parallel_chunks: parseInt(v, 10) })
          }
        >
          <SelectTrigger id="parallel-chunks">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PARALLELISM_PRESETS.map((p) => (
              <SelectItem key={p.value} value={String(p.value)}>
                {p.label} — {p.hint}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          More parallel pages = faster, but uses more RAM. Each in-flight page
          holds ~150-300 MB of BabelDOC state.
        </p>
      </div>
    </div>
  );
}
