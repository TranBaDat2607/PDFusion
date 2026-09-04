import { useEffect, useState } from "react";
import { ExternalLink, FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { tauriAvailable } from "@/lib/tauri-ready";

const REPO_URL = "https://github.com/TranBaDat2607/PDFusion";

interface AboutDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Version comes from the Tauri shell (`tauri.conf.json`'s `version`), which is
 * the number the installer, the Programs list and the .msi filename all carry —
 * i.e. the one a user reporting a bug can actually read off their machine.
 * Hard-coding it here is how it drifted to "0.2.0 — UI rewrite", a release that
 * never shipped.
 */
function useAppVersion(enabled: boolean): string | null {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    // `pnpm dev` in a plain browser tab has no shell to ask.
    if (!enabled || !tauriAvailable()) return;
    let cancelled = false;
    void import("@tauri-apps/api/app")
      .then(({ getVersion }) => getVersion())
      .then((v) => {
        if (!cancelled) setVersion(v);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return version;
}

export function AboutDialog({ open, onOpenChange }: AboutDialogProps) {
  const version = useAppVersion(open);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="mb-2 flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
              <FileText className="h-5 w-5 text-primary" />
            </div>
            <div>
              <DialogTitle>PDFusion</DialogTitle>
              <DialogDescription>
                {version ? `Version ${version}` : "Translate PDFs, keep the layout"}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <ul className="space-y-1.5 text-sm text-muted-foreground">
          <li>· PDF translation with layout preserved (BabelDOC)</li>
          <li>· Argos Translate — offline, no API key needed</li>
          <li>· OpenAI · Gemini · Anthropic Claude</li>
          <li>· RAG chat over the open document (hybrid search + HyDE)</li>
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button asChild>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="gap-2"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Docs
            </a>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
