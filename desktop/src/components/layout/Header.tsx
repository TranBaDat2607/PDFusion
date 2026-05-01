import { FileText, Info, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";

interface HeaderProps {
  onOpenSettings: () => void;
  onOpenAbout: () => void;
}

export function Header({ onOpenSettings, onOpenAbout }: HeaderProps) {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-background/80 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
          <FileText className="h-4 w-4 text-primary" />
        </div>
        <span className="text-sm font-semibold tracking-tight">PDFusion</span>
        <span className="text-xs text-muted-foreground">·</span>
        <span className="text-xs text-muted-foreground">
          PDF translator with AI chat
        </span>
      </div>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={onOpenSettings}
          className="gap-2"
        >
          <Settings className="h-4 w-4" />
          Settings
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onOpenAbout}
          aria-label="About"
        >
          <Info className="h-4 w-4" />
        </Button>
        <ThemeToggle />
      </div>
    </header>
  );
}
