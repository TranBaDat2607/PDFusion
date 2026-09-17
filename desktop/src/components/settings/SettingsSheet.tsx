import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  Loader2,
  ShieldCheck,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { ChatIndexTab } from "@/components/settings/ChatIndexTab";
import { ModelCombobox } from "@/components/settings/ModelCombobox";
import { Button } from "@/components/ui/button";
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

const ALL_SERVICES: ServiceCode[] = ["argos", "openai", "gemini", "anthropic"];

// Pseudo-tab values for the cache and chat panels — not translation services.
type TabValue = ServiceCode | "cache" | "chat";

// Short names for the tab row. The services' full names ("Argos Translate
// (offline)") are wider than a column and overlapped their neighbours; each
// stays available as its tab's tooltip.
const TAB_LABELS: Record<ServiceCode, string> = {
  argos: "Argos",
  openai: "OpenAI",
  gemini: "Gemini",
  anthropic: "Claude",
};

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
}

export function SettingsSheet({ open, onOpenChange }: SettingsSheetProps) {
  const { data: config } = useConfig();
  const { data: options } = useOptions();
  const updateConfig = useUpdateConfig();

  const [tab, setTab] = useState<TabValue>("argos");
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
                  {TAB_LABELS[c]}
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
  onChange: (patch: Partial<ServiceDraft>) => void;
}

function ServiceTab({
  code,
  option,
  saved,
  draft,
  problem,
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

interface CacheStats {
  entries: number;
  active: number;
  expired: number;
  size_mb: number;
  by_service: Record<string, number>;
  hits: number;
  misses: number;
  hit_rate: number;
  cache_dir: string;
  ttl_days: number;
  max_size_mb: number;
}

function CacheTab({ open }: { open: boolean }) {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState<"expired" | "all" | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const s = await api.get<CacheStats>("/config/cache");
      setStats(s);
    } catch (e) {
      toast.error("Failed to load cache stats", { description: (e as Error).message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) refresh();
  }, [open]);

  const handleClear = async (scope: "expired" | "all") => {
    setClearing(scope);
    try {
      // "Clear all" also wipes the whole-PDF cache (translated PDFs), not
      // just the paragraph cache; "Clear expired" is paragraph-only (the PDF
      // cache has no TTL).
      const target = scope === "all" ? "all" : "paragraph";
      const r = await api.delete<{ removed: number; scope: string }>(
        `/config/cache?scope=${scope}&target=${target}`,
      );
      toast.success(`Cleared ${r.removed} ${scope} entries`);
      await refresh();
    } catch (e) {
      toast.error("Failed to clear cache", { description: (e as Error).message });
    } finally {
      setClearing(null);
    }
  };

  return (
    <div className="space-y-4 py-2">
      <div className="rounded-md border border-border bg-muted/40 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Translation cache</span>
        </div>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Persists every translated paragraph on disk so re-translating the same
          PDF (or any paragraph you've seen before) is instant. Stored locally
          and never uploaded. Keyed by source text + language pair + service + model.
        </p>
      </div>

      {stats ? (
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Stat label="Entries" value={stats.active.toLocaleString()} />
          <Stat label="Size" value={`${stats.size_mb.toFixed(2)} MB`} />
          <Stat label="Session hits" value={stats.hits.toLocaleString()} />
          <Stat
            label="Hit rate"
            value={
              stats.hits + stats.misses === 0
                ? "—"
                : `${(stats.hit_rate * 100).toFixed(0)}%`
            }
          />
          <Stat label="Expired" value={stats.expired.toLocaleString()} />
          <Stat label="TTL" value={`${stats.ttl_days} days`} />
        </div>
      ) : (
        <div className="text-sm text-muted-foreground">
          {loading ? "Loading…" : "No data"}
        </div>
      )}

      {stats && Object.keys(stats.by_service).length > 0 && (
        <div className="text-xs text-muted-foreground">
          By service:{" "}
          {Object.entries(stats.by_service)
            .map(([s, n]) => `${s} (${n})`)
            .join(" · ")}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={refresh}
          disabled={loading}
        >
          {loading ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Refreshing…
            </>
          ) : (
            "Refresh"
          )}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleClear("expired")}
          disabled={clearing !== null}
        >
          {clearing === "expired" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Trash2 className="mr-2 h-4 w-4" />
          )}
          Clear expired
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => handleClear("all")}
          disabled={clearing !== null}
        >
          {clearing === "all" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Trash2 className="mr-2 h-4 w-4" />
          )}
          Clear all
        </Button>
      </div>

      {stats?.cache_dir && (
        <p className="text-xs text-muted-foreground break-all">
          Location: <code>{stats.cache_dir}</code>
        </p>
      )}
    </div>
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

function PerformanceSection() {
  const { data: config } = useConfig();
  const update = useUpdateConfig();
  const current = config?.processing?.max_parallel_chunks ?? 0;
  const cacheEnabled = config?.translation?.cache_translations ?? true;
  const pdfCacheEnabled = config?.translation?.cache_translated_pdfs ?? true;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">Performance</span>
      </div>

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

      <div className="space-y-2">
        <Label htmlFor="cache-translations" className="flex items-center gap-2">
          <Switch
            id="cache-translations"
            checked={cacheEnabled}
            onCheckedChange={(checked) =>
              update.mutate({ cache_translations: checked })
            }
          />
          <span className="text-sm">Cache translations to disk</span>
        </Label>
        <p className="text-xs text-muted-foreground pl-10">
          Re-translating a paragraph you've seen before is instant.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="cache-translated-pdfs" className="flex items-center gap-2">
          <Switch
            id="cache-translated-pdfs"
            checked={pdfCacheEnabled}
            onCheckedChange={(checked) =>
              update.mutate({ cache_translated_pdfs: checked })
            }
          />
          <span className="text-sm">Cache translated PDFs</span>
        </Label>
        <p className="text-xs text-muted-foreground pl-10">
          Reopen the same PDF and the translated copy loads in under a second —
          the layout / typeset / render pipeline is skipped entirely. Use
          Re-translate to force a fresh run.
        </p>
      </div>
    </div>
  );
}
