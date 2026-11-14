import { useEffect, useRef, useState } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  Clock,
  Cpu,
  Menu,
  MessagesSquare,
  Wifi,
  WifiOff,
} from "lucide-react";
import { cn } from "../lib/cn";
import { formatClock } from "../lib/format";
import { MODELS, type Mode } from "../types";

interface TopBarProps {
  model: string;
  mode: Mode;
  connected: boolean;
  now: Date;
  onModel: (model: string) => void;
  onMode: (mode: Mode) => void;
  onMenu: () => void;
  serverMode?: boolean;
  modelLocked?: boolean;
  agentDisabled?: boolean;
}

function shortModel(model: string): string {
  return model.replace(/-Q4_K_M$/i, "");
}

export function TopBar({
  model,
  mode,
  connected,
  now,
  onModel,
  onMode,
  onMenu,
  serverMode = false,
  modelLocked = false,
  agentDisabled = false,
}: TopBarProps) {
  const [open, setOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <header className="card relative z-20 flex items-center gap-2 px-3 py-2.5 sm:px-4">
      <button type="button" onClick={onMenu} className="icon-btn lg:hidden" aria-label="Open menu">
        <Menu className="h-4 w-4" />
      </button>

      {/* Model picker */}
      <div className="relative" ref={pickerRef}>
        <button
          type="button"
          onClick={() => !modelLocked && setOpen((value) => !value)}
          disabled={modelLocked}
          title={modelLocked ? "Model is set by the Gemma server" : "Switch model"}
          className="flex min-w-0 items-center gap-2 rounded-lg border border-line bg-[#151515] px-2.5 py-1.5 text-left transition-colors hover:border-[#3a3a3a] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-default disabled:hover:border-line"
          aria-haspopup={modelLocked ? undefined : "listbox"}
          aria-expanded={modelLocked ? undefined : open}
        >
          <Cpu className="h-4 w-4 shrink-0 text-accent" />
          <span className="min-w-0">
            <span className="block max-w-[190px] truncate font-mono text-[12.5px] font-medium text-text sm:max-w-[320px]">
              {shortModel(model)}
            </span>
            <span className="hidden text-[10px] uppercase tracking-wider text-muted sm:block">
              local model
            </span>
          </span>
          {!modelLocked && (
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 shrink-0 text-muted transition-transform",
                open && "rotate-180",
              )}
            />
          )}
        </button>

        {open && (
          <div
            role="listbox"
            className="absolute left-0 top-full z-30 mt-2 w-[320px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-line bg-[#1b1b1b] shadow-2xl"
          >
            <div className="border-b border-line px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted">
              Models
            </div>
            {MODELS.map((item) => (
              <button
                key={item}
                type="button"
                role="option"
                aria-selected={item === model}
                onClick={() => {
                  onModel(item);
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-white/[0.04]"
              >
                <Bot className="h-3.5 w-3.5 shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-dim">{item}</span>
                {item === model && <Check className="h-3.5 w-3.5 shrink-0 text-accent" />}
              </button>
            ))}
            <div className="border-t border-line px-3 py-2 text-[10.5px] text-muted">
              Switch with <span className="font-mono text-dim">/model &lt;name&gt;</span>
            </div>
          </div>
        )}
      </div>

      {/* Connection */}
      <span
        className={cn(
          "pill shrink-0 gap-1.5",
          connected
            ? "border-success/30 bg-success/10 text-success"
            : "border-danger/30 bg-danger/10 text-danger",
        )}
        title={
          connected
            ? serverMode
              ? "Paired with the Gemma TUI server"
              : "llama.cpp reachable"
            : "llama.cpp unreachable"
        }
      >
        {connected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
        <span className="hidden sm:inline">
          {connected ? (serverMode ? "Paired" : "Connected") : "Offline"}
        </span>
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            connected ? "animate-pulse bg-success" : "bg-danger",
          )}
        />
      </span>

      {/* Mode toggle */}
      <div className="ml-auto flex shrink-0 items-center gap-1 rounded-lg border border-line bg-[#151515] p-0.5">
        {(["chat", "agent"] as const).map((value) => {
          const active = mode === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onMode(value)}
              disabled={value === "agent" && agentDisabled}
              title={
                value === "agent" && agentDisabled
                  ? "Agent mode runs in the Gemma TUI"
                  : undefined
              }
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
                active ? "bg-accent/15 text-accent" : "text-muted hover:text-dim",
                value === "agent" && agentDisabled && "cursor-not-allowed opacity-40 hover:text-muted",
              )}
              aria-pressed={active}
            >
              {value === "chat" ? (
                <MessagesSquare className="h-3 w-3" />
              ) : (
                <Bot className="h-3 w-3" />
              )}
              <span className="hidden capitalize sm:inline">{value}</span>
            </button>
          );
        })}
      </div>

      {/* Clock */}
      <div className="hidden shrink-0 items-center gap-1.5 rounded-lg border border-line bg-[#151515] px-2.5 py-1.5 md:flex">
        <Clock className="h-3.5 w-3.5 text-muted" />
        <span className="font-mono text-[11.5px] tabular-nums text-dim">{formatClock(now)}</span>
      </div>
    </header>
  );
}
