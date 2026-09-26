import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  Eye,
  EyeOff,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  useConfig,
  useOptions,
  useUpdateConfig,
  type OptionsResponse,
} from "@/hooks/useConfig";
import {
  PROVIDERS_KEY,
  refreshProviderModels,
  useProviderModels,
  useProviders,
  useSaveProvider,
  verifyProvider,
  type ModelCatalog,
  type ProviderInfo,
} from "@/hooks/useProviders";
import {
  addCustomModel,
  draftFrom,
  draftProblem,
  endpointNeedsKey,
  hasSavedKey,
  isValidEndpoint,
  modelRows,
  needsVerify,
  providerUpdate,
  seedEnabled,
  selectsProvider,
  statusLine,
  toggleModel,
  verifyRequest,
  type ProviderDraft,
} from "@/lib/provider-draft";
import { cn } from "@/lib/utils";

/** The DOM id of a provider's card, which the pickers' "Add an API key"
 *  scrolls to. */
export const providerCardId = (id: string) => `provider-card-${id}`;

interface ModelsTabProps {
  /** Opened from a picker for this provider: scroll to its card, and start
   *  in its key field when it has none. */
  focusProvider?: string;
}

/**
 * Settings → Models (#86): one card per provider from `GET /providers`, each
 * saving on its own. The sheet used to have a tab per provider and one Save
 * for all of them.
 */
export function ModelsTab({ focusProvider }: ModelsTabProps) {
  const providers = useProviders();
  const { data: options } = useOptions();

  useEffect(() => {
    if (!focusProvider || !providers.data) return;
    document
      .getElementById(providerCardId(focusProvider))
      ?.scrollIntoView({ block: "start" });
  }, [focusProvider, providers.data]);

  if (providers.isError) {
    return (
      <p className="py-2 text-sm text-destructive">
        Could not load the providers: {providers.error.message}
      </p>
    );
  }
  if (!providers.data) {
    return <p className="py-2 text-sm text-muted-foreground">Loading…</p>;
  }
  return (
    <div className="space-y-4 py-2">
      {providers.data.map((provider) =>
        provider.model_is_fixed ? (
          <OfflineCard key={provider.id} provider={provider} options={options} />
        ) : (
          <ProviderCard
            key={provider.id}
            provider={provider}
            focused={provider.id === focusProvider}
            focusProvider={focusProvider}
          />
        ),
      )}
    </div>
  );
}

function CardHeader({ provider }: { provider: ProviderInfo }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{provider.label}</div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {provider.description}
        </p>
      </div>
      {provider.signup_url && (
        <a
          href={provider.signup_url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex shrink-0 items-center gap-1 text-xs text-primary hover:underline"
        >
          Get a key
          <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </div>
  );
}

interface ProviderCardProps {
  provider: ProviderInfo;
  focused: boolean;
  focusProvider?: string;
}

function ProviderCard({ provider, focused, focusProvider }: ProviderCardProps) {
  const qc = useQueryClient();
  const { data: config } = useConfig();
  const updateConfig = useUpdateConfig();
  const save = useSaveProvider();

  const [draft, setDraft] = useState<ProviderDraft>(() => draftFrom(provider));
  const [showKey, setShowKey] = useState(false);
  // Why the last Verify or Save stopped, or what Verify found.
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  // The provider turned the draft down. Saving again skips the check: a local
  // server may just not be running yet.
  const [saveAnyway, setSaveAnyway] = useState(false);
  const [busy, setBusy] = useState<"verifying" | "saving" | null>(null);
  // What a typed key's listing found. The catalog only describes the saved
  // key, so until this key is saved its models come from here.
  const [typedListing, setTypedListing] = useState<ModelCatalog | null>(null);
  const [modelsOpen, setModelsOpen] = useState(false);
  const [endpointOpen, setEndpointOpen] = useState(!!provider.base_url);

  const catalog = useProviderModels(provider, modelsOpen && hasSavedKey(provider));
  // A listing of the saved key moves its state on the sidecar (`valid`,
  // `invalid`); refetch the providers so the status line follows.
  useEffect(() => {
    if (catalog.data && catalog.data.key_state !== provider.key_state) {
      void qc.invalidateQueries({ queryKey: PROVIDERS_KEY, exact: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog.data]);

  const edit = (next: ProviderDraft, keepListing = true) => {
    setDraft(next);
    setNotice(null);
    setSaveAnyway(false);
    if (!keepListing) setTypedListing(null);
  };

  const listing = typedListing ?? catalog.data;
  const update = providerUpdate(draft, provider);
  const dirty = Object.keys(update).length > 0;
  const toVerify = needsVerify(draft, provider);
  const status = statusLine(provider, catalog.data, Date.now());

  /** List with the draft's key and endpoint. The listing is kept for the
   *  model list, and seeds the enabled models when none are yet. */
  const runVerify = async (): Promise<ProviderDraft | null> => {
    const request = verifyRequest(draft, provider);
    if (!request) {
      setNotice({ ok: false, text: "Enter an API key first." });
      return null;
    }
    setBusy("verifying");
    try {
      const result = await verifyProvider(provider.id, request);
      if (!result.valid) {
        setNotice({ ok: false, text: result.message });
        return null;
      }
      const seeded = seedEnabled(draft, provider, result.models.map((m) => m.id));
      setDraft(seeded);
      setTypedListing({
        models: result.models,
        hidden: result.hidden,
        key_state: result.key_state,
        fetched_at: null,
        error: null,
      });
      setModelsOpen(true);
      setNotice({ ok: true, text: result.message });
      return seeded;
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
      return null;
    } finally {
      setBusy(null);
    }
  };

  const handleVerify = async () => {
    const problem = draftProblem(draft, provider);
    if (problem) {
      setNotice({ ok: false, text: problem });
      return;
    }
    await runVerify();
  };

  const handleSave = async () => {
    const problem = draftProblem(draft, provider);
    if (problem) {
      setNotice({ ok: false, text: problem });
      return;
    }
    let toSave = draft;
    const anyway = saveAnyway;
    if (toVerify && !anyway) {
      const verified = await runVerify();
      if (!verified) {
        setSaveAnyway(true);
        return;
      }
      toSave = verified;
    }
    const body = providerUpdate(toSave, provider);
    if (Object.keys(body).length === 0) return;
    setBusy("saving");
    try {
      const saved = await save.mutateAsync({ id: provider.id, update: body });
      const translating = config?.translation.model.provider ?? "";
      // Opened to add this provider's key, or still on the offline engine:
      // a key that just listed its models is the one to translate with. The
      // sidecar used to do this itself for `PUT /config`; `PUT /providers`
      // leaves the choice to the caller.
      if (
        selectsProvider({
          provider,
          update: body,
          translationProvider: translating,
          openedFor: focusProvider,
          savedAnyway: anyway,
        })
      ) {
        await updateConfig.mutateAsync({
          translation_model: { provider: saved.id, model: saved.model },
        });
      }
      setDraft(draftFrom(saved));
      setTypedListing(null);
      setSaveAnyway(false);
      setNotice(null);
      toast.success(`${provider.label} saved`);
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const refresh = async () => {
    try {
      await refreshProviderModels(qc, provider);
    } catch (e) {
      toast.error("Could not list the models", { description: (e as Error).message });
    }
  };

  const savedKey = hasSavedKey(provider);
  const keyPlaceholder =
    provider.key_state === "unreadable" && !draft.clearKey
      ? "Saved key couldn't be read — enter it again"
      : savedKey && !draft.clearKey
        ? "•••••••• (saved)"
        : "Paste key…";

  return (
    <section
      id={providerCardId(provider.id)}
      className={cn(
        "scroll-mt-2 space-y-3 rounded-lg border border-border p-4",
        focused && "border-primary/60",
      )}
    >
      <CardHeader provider={provider} />

      <StatusText tone={status.tone} text={status.text} />

      {provider.requires_key && (
        <div className="space-y-1.5">
          <Label htmlFor={`${provider.id}-key`}>API key</Label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                id={`${provider.id}-key`}
                type={showKey ? "text" : "password"}
                autoComplete="off"
                autoFocus={focused && !provider.has_key}
                value={draft.apiKey}
                placeholder={keyPlaceholder}
                onChange={(e) =>
                  edit({ ...draft, apiKey: e.target.value, clearKey: false }, false)
                }
              />
              <button
                type="button"
                onClick={() => setShowKey((s) => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label={showKey ? "Hide key" : "Show key"}
              >
                {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            <Button
              variant="outline"
              onClick={() => edit({ ...draft, apiKey: "", clearKey: true }, false)}
              disabled={!draft.apiKey && (!savedKey || draft.clearKey)}
            >
              Clear
            </Button>
          </div>
          {draft.clearKey && (
            <p className="text-xs text-muted-foreground">
              The saved key is removed when you save.
            </p>
          )}
        </div>
      )}

      {provider.takes_endpoint && (
        <Collapsible open={endpointOpen} onOpenChange={setEndpointOpen}>
          <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ChevronRight
              className={cn("h-3.5 w-3.5 transition-transform", endpointOpen && "rotate-90")}
            />
            Override base URL
            {provider.base_url && (
              <span className="ml-1 font-mono text-[11px]">({provider.base_url})</span>
            )}
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-2 space-y-1.5">
            <Input
              aria-label={`${provider.label} base URL`}
              value={draft.baseUrl}
              placeholder={provider.default_base_url ?? undefined}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={!isValidEndpoint(draft.baseUrl) || undefined}
              className="font-mono"
              onChange={(e) => edit({ ...draft, baseUrl: e.target.value }, false)}
            />
            {provider.endpoint_hint && (
              <p className="text-xs text-muted-foreground">{provider.endpoint_hint}</p>
            )}
            {endpointNeedsKey(draft, provider) && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                Enter the API key again for the new endpoint: a saved key is only
                sent to the endpoint it was saved for.
              </p>
            )}
          </CollapsibleContent>
        </Collapsible>
      )}

      <Collapsible open={modelsOpen} onOpenChange={setModelsOpen}>
        <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ChevronRight
            className={cn("h-3.5 w-3.5 transition-transform", modelsOpen && "rotate-90")}
          />
          Models
          <span className="ml-1">
            ({draft.enabled.length === 0 ? "none chosen" : `${draft.enabled.length} on`})
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2">
          <ModelList
            listing={listing}
            loading={catalog.isFetching && !listing}
            canRefresh={savedKey && !typedListing}
            refreshing={catalog.isFetching}
            onRefresh={refresh}
            draft={draft}
            onChange={(next) => edit(next)}
          />
        </CollapsibleContent>
      </Collapsible>

      {notice && <StatusText tone={notice.ok ? "ok" : "error"} text={notice.text} />}

      <div className="flex flex-wrap justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={busy !== null || !verifyRequest(draft, provider)}
          onClick={handleVerify}
        >
          {busy === "verifying" && !dirty ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : null}
          Verify →
        </Button>
        <Button size="sm" disabled={busy !== null || !dirty} onClick={handleSave}>
          {busy !== null && dirty && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {saveAnyway ? "Save anyway" : toVerify ? "Verify & save" : "Save"}
        </Button>
      </div>
    </section>
  );
}

interface ModelListProps {
  listing: ModelCatalog | null | undefined;
  loading: boolean;
  canRefresh: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  draft: ProviderDraft;
  onChange: (draft: ProviderDraft) => void;
}

/** The models a key can use, a switch each: what is on is what the pickers
 *  offer (`enabled_models`). */
function ModelList({
  listing,
  loading,
  canRefresh,
  refreshing,
  onRefresh,
  draft,
  onChange,
}: ModelListProps) {
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [custom, setCustom] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(
    () => modelRows(listing ?? undefined, draft, { search, showAll }),
    [listing, draft, search, showAll],
  );
  const hiddenCount = listing?.hidden.length ?? 0;

  const addCustom = () => {
    const next = addCustomModel(draft, custom);
    if (next.enabled.length !== draft.enabled.length) onChange(next);
    setCustom("");
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Search models"
            value={search}
            placeholder="Search models…"
            className="h-8 pl-7 text-xs"
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {canRefresh && (
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="List the models again"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
          </Button>
        )}
      </div>

      <div
        ref={listRef}
        className="max-h-56 overflow-y-auto rounded-md border border-border"
      >
        {loading ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">Listing models…</p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            {search ? "No model matches." : "Verify a key to see its models."}
          </p>
        ) : (
          rows.map((row) => (
            <label
              key={`${row.kind}-${row.id}`}
              className="flex cursor-pointer items-center gap-2 border-b border-border px-3 py-1.5 last:border-b-0 hover:bg-muted/40"
            >
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{row.id}</span>
              {row.kind !== "listed" && (
                <span className="text-[10px] text-muted-foreground">
                  {row.kind === "custom"
                    ? "added"
                    : row.kind === "hidden"
                      ? "not a chat model?"
                      : "suggested"}
                </span>
              )}
              <Switch
                aria-label={`Offer ${row.id}`}
                checked={row.enabled}
                onCheckedChange={(on) => onChange(toggleModel(draft, row.id, on))}
              />
            </label>
          ))
        )}
      </div>

      {hiddenCount > 0 && (
        <button
          type="button"
          className="text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setShowAll((s) => !s)}
        >
          {showAll
            ? "Hide models that don't look like chat models"
            : `Show all (${hiddenCount} more that don't look like chat models)`}
        </button>
      )}

      <div className="flex gap-2">
        <Input
          aria-label="Custom model name"
          value={custom}
          placeholder="Add a model the list doesn't have…"
          autoComplete="off"
          spellCheck={false}
          className="h-8 font-mono text-xs"
          onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addCustom();
            }
          }}
        />
        <Button
          variant="outline"
          size="sm"
          className="h-8"
          disabled={!custom.trim()}
          onClick={addCustom}
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add
        </Button>
      </div>
    </div>
  );
}

function StatusText({ tone, text }: { tone: "ok" | "error" | "muted"; text: string }) {
  return (
    <p
      className={cn(
        "flex items-start gap-1.5 text-xs",
        tone === "ok" && "text-primary",
        tone === "error" && "text-destructive",
        tone === "muted" && "text-muted-foreground",
      )}
    >
      {tone === "ok" && <CheckCircle2 className="mt-px h-3.5 w-3.5 shrink-0" />}
      {tone === "error" && <XCircle className="mt-px h-3.5 w-3.5 shrink-0" />}
      <span className="min-w-0 break-words">{text}</span>
    </p>
  );
}

/** The language pairs a restricted engine ships, as "EN → VI". */
function pairLabels(options: OptionsResponse | undefined, provider: ProviderInfo): string[] {
  const pairs = options?.services.find((s) => s.code === provider.id)?.supported_pairs;
  if (!pairs) return [];
  return pairs
    .filter(([source]) => source !== "auto")
    .map(([source, target]) => `${source.toUpperCase()} → ${target.toUpperCase()}`);
}

/**
 * The offline engine: no key, no models to choose. What it needs is its
 * language pack, which the one-time setup step installs.
 */
function OfflineCard({
  provider,
  options,
}: {
  provider: ProviderInfo;
  options: OptionsResponse | undefined;
}) {
  const [checking, setChecking] = useState(false);
  const [status, setStatus] = useState<{ valid: boolean; message: string } | null>(null);
  const pairs = pairLabels(options, provider);

  // Verify, for an engine with no key and no models to list, checks its
  // install: its language pack.
  const check = async () => {
    setChecking(true);
    try {
      setStatus(await verifyProvider(provider.id, {}));
    } catch (e) {
      setStatus({ valid: false, message: (e as Error).message });
    } finally {
      setChecking(false);
    }
  };

  return (
    <section
      id={providerCardId(provider.id)}
      className="scroll-mt-2 space-y-3 rounded-lg border border-border bg-muted/40 p-4"
    >
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">{provider.label}</span>
        <span className="rounded bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">
          Offline
        </span>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{provider.description}</p>
      {pairs.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {pairs.map((pair) => (
            <span
              key={pair}
              className="rounded-md bg-background px-2 py-1 font-mono text-muted-foreground"
            >
              {pair}
            </span>
          ))}
          <span className="text-muted-foreground">Other languages need an LLM key.</span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" size="sm" onClick={check} disabled={checking}>
          {checking && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Check install
        </Button>
        {status && (
          <StatusText tone={status.valid ? "ok" : "error"} text={status.message} />
        )}
      </div>
    </section>
  );
}
