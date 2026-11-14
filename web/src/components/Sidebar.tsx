import { useMemo, useState } from "react";
import { MessageSquare, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import { cn } from "../lib/cn";
import { shortSessionId, timeAgo } from "../lib/format";
import { ACCENTS, type AccentPreset, type Session } from "../types";

interface SidebarProps {
  sessions: Session[];
  activeId: string;
  accent: AccentPreset;
  streaming: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onAccent: (id: string) => void;
  onClose?: () => void;
}

export function Sidebar({
  sessions,
  activeId,
  accent,
  streaming,
  onNew,
  onSelect,
  onDelete,
  onAccent,
  onClose,
}: SidebarProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const sorted = [...sessions].sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
    if (!needle) return sorted;
    return sorted.filter((session) => session.title.toLowerCase().includes(needle));
  }, [query, sessions]);

  return (
    <div className="panel flex h-full min-h-0 flex-col overflow-hidden">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 pb-4 pt-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent shadow-glow">
          <Sparkles className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-[15px] font-semibold leading-tight text-text">
            Gemma
            <span className="rounded border border-line px-1 py-px text-[9px] font-medium uppercase tracking-wider text-muted">
              web
            </span>
          </div>
          <div className="truncate text-[11px] text-muted">local · E4B · llama.cpp</div>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="icon-btn lg:hidden" aria-label="Close menu">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-[13px] font-medium text-accent transition-colors hover:bg-accent/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <Plus className="h-4 w-4" />
          New Chat
          <span className="ml-auto rounded border border-accent/25 bg-black/20 px-1.5 py-px font-mono text-[10px] text-accent/80">
            ⌘N
          </span>
        </button>
      </div>

      {/* Search */}
      <div className="px-3 pb-3 pt-3">
        <label className="flex items-center gap-2 rounded-lg border border-line bg-[#151515] px-2.5 py-1.5 focus-within:border-accent/40">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search chats"
            className="min-w-0 flex-1 bg-transparent text-[12.5px] text-text outline-none placeholder:text-muted"
          />
        </label>
      </div>

      {/* Recent chats */}
      <div className="flex min-h-0 flex-1 flex-col px-3">
        <div className="flex items-center justify-between px-1 pb-2">
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted">
            Recent Chats
          </span>
          <span className="rounded-full border border-line px-1.5 py-px font-mono text-[10px] text-muted">
            {sessions.length}
          </span>
        </div>

        <nav className="-mx-1 min-h-0 flex-1 space-y-1 overflow-y-auto px-1 pb-2">
          {filtered.length === 0 && (
            <p className="px-2 py-6 text-center text-[12px] text-muted">No chats match “{query}”.</p>
          )}
          {filtered.map((session) => {
            const active = session.id === activeId;
            return (
              <div key={session.id} className="group/chat relative">
                <button
                  type="button"
                  onClick={() => onSelect(session.id)}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
                    active
                      ? "border-accent/25 bg-accent/[0.07]"
                      : "border-transparent hover:border-line hover:bg-white/[0.025]",
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border",
                      active
                        ? "border-accent/30 bg-accent/10 text-accent"
                        : "border-line bg-white/[0.02] text-muted",
                    )}
                  >
                    <MessageSquare className="h-3 w-3" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block truncate text-[12.5px] leading-5",
                        active ? "font-medium text-text" : "text-dim",
                      )}
                    >
                      {session.title}
                    </span>
                    <span className="flex items-center gap-1.5 text-[10.5px] text-muted">
                      <span>{timeAgo(session.updatedAt)}</span>
                      <span className="text-line">·</span>
                      <span className="truncate font-mono">{shortSessionId(session.id)}</span>
                      {active && streaming && (
                        <span className="ml-auto flex items-center gap-1 text-accent">
                          <span className="h-1 w-1 animate-pulse rounded-full bg-accent" />
                          live
                        </span>
                      )}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(session.id);
                  }}
                  className="absolute right-1.5 top-1.5 rounded-md p-1 text-muted opacity-100 transition-all hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 sm:opacity-0 sm:group-hover/chat:opacity-100"
                  aria-label={`Delete ${session.title}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </nav>
      </div>

      {/* Theme swatches */}
      <div className="border-t border-line/70 px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted">
            Accent
          </span>
          <span className="font-mono text-[10px] text-muted">{accent.name}</span>
        </div>
        <div className="flex items-center gap-2">
          {ACCENTS.map((preset) => {
            const selected = preset.id === accent.id;
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => onAccent(preset.id)}
                title={preset.name}
                aria-label={`${preset.name} accent`}
                aria-pressed={selected}
                className={cn(
                  "h-4 w-4 rounded-full border transition-transform hover:scale-110",
                  selected ? "border-white/70" : "border-transparent opacity-70",
                )}
                style={{
                  backgroundColor: `rgb(${preset.rgb})`,
                  boxShadow: selected ? `0 0 0 2px #181818, 0 0 12px rgb(${preset.rgb} / 0.5)` : undefined,
                }}
              />
            );
          })}
          <span className="ml-auto font-mono text-[10px] text-muted">/theme</span>
        </div>
      </div>
    </div>
  );
}
