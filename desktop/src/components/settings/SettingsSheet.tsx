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
  type ServiceCode,
  type ServiceOption,
} from "@/hooks/useConfig";

// LLM service codes — exclude argos (no api_key / model edits).
type LlmServiceCode = Exclude<ServiceCode, "argos">;
const LLM_SERVICES: LlmServiceCode[] = ["openai", "gemini", "anthropic"];
const ALL_SERVICES: ServiceCode[] = ["argos", "openai", "gemini", "anthropic"];

// Pseudo-tab value for the cache panel — not a real translation service.
type TabValue = ServiceCode | "cache";

interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface DraftService {
  apiKey: string;          // empty = unchanged from server-side state
  apiKeyTouched: boolean;  // user actually typed something
  model: string;
}

type Drafts = Record<LlmServiceCode, DraftService>;

const EMPTY_DRAFTS: Drafts = {
  openai: { apiKey: "", apiKeyTouched: false, model: "" },
  gemini: { apiKey: "", apiKeyTouched: false, model: "" },
  anthropic: { apiKey: "", apiKeyTouched: false, model: "" },
};

export function SettingsSheet({ open, onOpenChange }: SettingsSheetProps) {
  const { data: config } = useConfig();
  const { data: options } = useOptions();
  const updateConfig = useUpdateConfig();

  const [tab, setTab] = useState<TabValue>("argos");
  const [drafts, setDrafts] = useState<Drafts>(EMPTY_DRAFTS);

  // Reset drafts whenever the sheet opens (or config changes)
  useEffect(() => {
    if (!open || !config) return;
    setDrafts({
      openai: { apiKey: "", apiKeyTouched: false, model: config.openai.model },
      gemini: { apiKey: "", apiKeyTouched: false, model: config.gemini.model },
      anthropic: {
        apiKey: "",
        apiKeyTouched: false,
        model: config.anthropic.model,
      },
    });
  }, [open, config]);

  const handleSave = async () => {
    const update: Parameters<typeof updateConfig.mutate>[0] = {};
    (Object.entries(drafts) as Array<[LlmServiceCode, DraftService]>).forEach(
      ([code, d]) => {
        const change: { api_key?: string | null; model?: string } = {};
        if (d.apiKeyTouched) change.api_key = d.apiKey || null;
        if (config && d.model !== config[code].model) change.model = d.model;
        if (Object.keys(change).length > 0) update[code] = change;
      },
    );

    if (Object.keys(update).length === 0) {
      onOpenChange(false);
      return;
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
            Manage API keys and models for each translation service. Keys are
            encrypted at rest.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4">
          <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
            <TabsList className="grid w-full grid-cols-5">
              {ALL_SERVICES.map((c) => (
                <TabsTrigger key={c} value={c}>
                  {options?.services.find((s) => s.code === c)?.label ?? c}
                </TabsTrigger>
              ))}
              <TabsTrigger value="cache">Cache</TabsTrigger>
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

            {LLM_SERVICES.map((code) => {
              const opt = options?.services.find((s) => s.code === code);
              if (!opt) return null;
              return (
                <TabsContent key={code} value={code} className="mt-4">
                  <ServiceTab
                    code={code}
                    option={opt}
                    hasExistingKey={!!config?.[code].has_key}
                    draft={drafts[code]}
                    onChange={(patch) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [code]: { ...prev[code], ...patch },
                      }))
                    }
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
          <Button onClick={handleSave} disabled={updateConfig.isPending}>
            {updateConfig.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving…
              </>
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
          fees, no data leaves your computer. Used automatically when no LLM
          key is configured.
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
          The language pack (~80 MB) downloads automatically the first time you
          translate.
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
  hasExistingKey: boolean;
  draft: DraftService;
  onChange: (patch: Partial<DraftService>) => void;
}

function ServiceTab({
  code,
  option,
  hasExistingKey,
  draft,
  onChange,
}: ServiceTabProps) {
  const [show, setShow] = useState(false);
  const validate = useValidateCredentials();
  const [validation, setValidation] = useState<{
    valid: boolean;
    message: string;
  } | null>(null);

  const handleValidate = async () => {
    const apiKey = draft.apiKey.trim();
    if (!apiKey) {
      setValidation({
        valid: false,
        message: "Enter an API key first",
      });
      return;
    }
    try {
      const result = await validate.mutateAsync({
        service: code,
        api_key: apiKey,
        model: draft.model,
      });
      setValidation(result);
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
              placeholder={hasExistingKey ? "•••••••• (saved)" : "Paste key…"}
              onChange={(e) =>
                onChange({ apiKey: e.target.value, apiKeyTouched: true })
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
            onClick={() =>
              onChange({ apiKey: "", apiKeyTouched: true })
            }
            disabled={!draft.apiKey && !hasExistingKey}
          >
            Clear
          </Button>
        </div>
        {hasExistingKey && !draft.apiKeyTouched && (
          <p className="text-xs text-muted-foreground">
            A key is currently saved. Type a new one to replace, or click Clear
            and Save to remove.
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor={`${code}-model`}>Model</Label>
        <Select
          value={draft.model}
          onValueChange={(model) => onChange({ model })}
        >
          <SelectTrigger id={`${code}-model`}>
            <SelectValue placeholder="Select model" />
          </SelectTrigger>
          <SelectContent>
            {option.models.map((m) => (
              <SelectItem key={m} value={m}>
                {m}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={handleValidate}
          disabled={validate.isPending || (!draft.apiKey && !draft.apiKeyTouched)}
          variant="secondary"
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
            className={`flex items-center gap-1 text-sm ${
              validation.valid ? "text-primary" : "text-destructive"
            }`}
          >
            {validation.valid ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            {validation.message}
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
      const r = await api.delete<{ removed: number; scope: string }>(
        `/config/cache?scope=${scope}`,
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
    </div>
  );
}
