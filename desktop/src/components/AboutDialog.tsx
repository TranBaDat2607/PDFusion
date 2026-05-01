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

interface AboutDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AboutDialog({ open, onOpenChange }: AboutDialogProps) {
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
              <DialogDescription>Version 0.2.0 — UI rewrite</DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <ul className="space-y-1.5 text-sm text-muted-foreground">
          <li>· PDF translation with BabelDOC</li>
          <li>· OpenAI · Gemini · Anthropic Claude</li>
          <li>· RAG chat with hybrid search + HyDE</li>
          <li>· Deep multi-hop academic search</li>
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button asChild>
            <a
              href="https://github.com/anthropics/claude-code"
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
