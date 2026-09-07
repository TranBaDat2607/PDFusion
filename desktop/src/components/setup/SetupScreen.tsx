import { AlertTriangle, Check, Download, FileText, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { EngineSetupState } from "@/hooks/useEngineSetup";
import type { EngineAssetGroup } from "@/lib/engine-setup";

interface SetupScreenProps {
  state: EngineSetupState;
  onInstall: () => void;
  onSkip: () => void;
  /**
   * Reached from a Translate that came back 409 rather than from first launch.
   * Only changes the copy — the user already knows they clicked Translate, so
   * the screen has to explain why it appeared instead.
   */
  forced: boolean;
}

function AssetRow({ group }: { group: EngineAssetGroup }) {
  const percent = group.total > 0 ? (group.present / group.total) * 100 : 0;
  return (
    <div className="space-y-1.5 rounded-md border border-border bg-muted/40 p-3 text-left">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium">{group.label}</span>
        {group.ready ? (
          <span className="flex items-center gap-1 text-xs text-primary">
            <Check className="h-3.5 w-3.5" />
            Installed
          </span>
        ) : (
          <span className="text-xs tabular-nums text-muted-foreground">
            {/* A group of one reads as a state, not a count: "0 of 1 files" is
                a worse way to say "not installed yet". */}
            {group.total > 1 ? `${group.present} of ${group.total} files` : "Not installed"}
          </span>
        )}
      </div>
      <Progress value={group.ready ? 100 : percent} className="h-1.5" />
      <p className="text-xs text-muted-foreground">{group.detail}</p>
    </div>
  );
}

/**
 * First run, before the workspace: install the assets a translation needs.
 *
 * They are ~290 MB — BabelDOC's layout models, fonts and character maps, plus
 * the Argos language pack — and until this existed they arrived as an
 * unexplained stall on "Initializing translator", or, offline, as the string
 * `1`. See `engine_assets.py`.
 *
 * There is always a way past: the assets are only needed for *translating*, and
 * opening and reading a PDF should never be held up by a download.
 */
export function SetupScreen({ state, onInstall, onSkip, forced }: SetupScreenProps) {
  const engine = state.engine;
  const installing = engine?.install.running ?? false;
  const bundled = engine?.bundled ?? false;
  const groups = engine?.groups ?? [];
  // A probe failure and an install failure are different things, but neither
  // leaves the user anywhere to go except Try again, so they share the line.
  const error = engine?.install.error ?? state.probeError;

  const lead = forced
    ? "Translating needs PDFusion's offline engine, which isn't installed yet."
    : "PDFusion needs its translation engine before it can translate a document.";
  const cost = bundled
    ? "Everything is included with the app — this unpacks it, no internet needed."
    : "About 290 MB, downloaded once from github.com and huggingface.co.";

  return (
    <div className="flex h-full w-full items-center justify-center bg-background p-6">
      <div className="flex w-full max-w-md flex-col items-center gap-5 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
          <FileText className="h-7 w-7 text-primary" />
        </div>
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Set up PDFusion</h1>
          <p className="text-sm text-muted-foreground">{lead}</p>
          <p className="text-xs text-muted-foreground">{cost}</p>
        </div>

        <div className="w-full space-y-2">
          {groups.map((group) => (
            <AssetRow key={group.id} group={group} />
          ))}
        </div>

        {installing && (
          <p
            role="status"
            className="flex items-center justify-center gap-2 text-sm text-muted-foreground"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {engine?.install.stage ?? "Installing…"}
          </p>
        )}

        {!installing && error && (
          <p className="flex items-start gap-1.5 text-left text-xs text-destructive">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </p>
        )}

        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button size="sm" onClick={onInstall} disabled={installing}>
            {installing ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="mr-1.5 h-3.5 w-3.5" />
            )}
            {error ? "Try again" : bundled ? "Install" : "Download and install"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onSkip} disabled={installing}>
            Not now
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          You can open and read PDFs without this. Only translating needs it.
        </p>
      </div>
    </div>
  );
}
