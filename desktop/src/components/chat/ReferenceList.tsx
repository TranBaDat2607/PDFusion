import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, Quote } from "lucide-react";

export interface ReferenceItem {
  key: string;
  label: string;
  detail?: string;
  page?: number;
  /** The answer cites this page. */
  cited?: boolean;
  /** How the page was read, when not just from its text. */
  note?: string;
}

interface ReferenceListProps {
  title: string;
  items: ReferenceItem[];
  onItemClick?: (item: ReferenceItem) => void;
}

export function ReferenceList({ title, items, onItemClick }: ReferenceListProps) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className="rounded-md border border-border/50 bg-muted/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium hover:bg-accent/50"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <span>{title}</span>
      </button>
      {open && (
        <ul className="divide-y divide-border/40 px-3 pb-2">
          {items.map((item) => (
            <li key={item.key}>
              <button
                type="button"
                onClick={() => onItemClick?.(item)}
                className="flex w-full items-start gap-2 py-2 text-left text-xs hover:text-primary"
              >
                {item.cited ? (
                  <Quote className="mt-0.5 h-3 w-3 shrink-0 text-primary" aria-label="Cited" />
                ) : (
                  <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
                )}
                <div className="flex-1 space-y-0.5 min-w-0">
                  <div className="truncate">
                    <span className="font-medium">{item.label}</span>
                    {item.cited && <span className="ml-1.5 text-primary">cited</span>}
                    {item.note && (
                      <span className="ml-1.5 text-muted-foreground">· {item.note}</span>
                    )}
                  </div>
                  {item.detail && (
                    <div className="text-muted-foreground line-clamp-2">
                      {item.detail}
                    </div>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
