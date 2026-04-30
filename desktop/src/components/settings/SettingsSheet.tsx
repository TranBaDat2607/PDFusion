import { useEffect, useState } from "react";
import { CheckCircle2, Eye, EyeOff, Loader2, XCircle } from "lucide-react";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import {
  useConfig,
  useOptions,
  useUpdateConfig,
  useValidateCredentials,
  type ServiceOption,
} from "@/hooks/useConfig";

type ServiceCode = "openai" | "gemini" | "anthropic";

interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface DraftService {
  apiKey: string;          // empty = unchanged from server-side state
  apiKeyTouched: boolean;  // user actually typed something
  model: string;
}

type Drafts = Record<ServiceCode, DraftService>;

const EMPTY_DRAFTS: Drafts = {
  openai: { apiKey: "", apiKeyTouched: false, model: "" },
  gemini: { apiKey: "", apiKeyTouched: false, model: "" },
  anthropic: { apiKey: "", apiKeyTouched: false, model: "" },
};

export function SettingsSheet({ open, onOpenChange }: SettingsSheetProps) {
  const { data: config } = useConfig();
  const { data: options } = useOptions();
  const updateConfig = useUpdateConfig();

  const [tab, setTab] = useState<ServiceCode>("openai");
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
    (Object.entries(drafts) as Array<[ServiceCode, DraftService]>).forEach(
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
          <Tabs value={tab} onValueChange={(v) => setTab(v as ServiceCode)}>
            <TabsList className="grid w-full grid-cols-3">
              {(["openai", "gemini", "anthropic"] as ServiceCode[]).map((c) => (
                <TabsTrigger key={c} value={c}>
                  {options?.services.find((s) => s.code === c)?.label ?? c}
                </TabsTrigger>
              ))}
            </TabsList>

            {(["openai", "gemini", "anthropic"] as ServiceCode[]).map((code) => {
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

interface ServiceTabProps {
  code: ServiceCode;
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
