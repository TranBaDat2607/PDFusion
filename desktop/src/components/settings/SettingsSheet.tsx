import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, FileText, Loader2, RefreshCw, Sparkles, Trash2 } from "lucide-react";

import { ChatIndexTab } from "@/components/settings/ChatIndexTab";
import { ModelsTab } from "@/components/settings/ModelsTab";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { useConfig, useUpdateConfig } from "@/hooks/useConfig";
import { cn } from "@/lib/utils";

type TabValue = "models" | "cache" | "chat";

interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Open on Models, at this provider's card. Unset opens on Models at the
   *  top. */
  focusProvider?: string;
}

export function SettingsSheet({ open, onOpenChange, focusProvider }: SettingsSheetProps) {
  const [tab, setTab] = useState<TabValue>("models");
  // Reset on opening only, never while open: a Cache switch refetches the
  // config and must not move the user off the tab they're on.
  const [wasOpen, setWasOpen] = useState(false);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setTab("models");
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Settings</SheetTitle>
          <SheetDescription>
            Keys, endpoints and models for each provider, each saved on its own
            card. Keys are encrypted at rest.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4">
          <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="models">Models</TabsTrigger>
              <TabsTrigger value="cache">Cache</TabsTrigger>
              <TabsTrigger value="chat">Chat</TabsTrigger>
            </TabsList>

            <TabsContent value="models" className="mt-4">
              <ModelsTab focusProvider={focusProvider} />
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
          </Tabs>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
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
