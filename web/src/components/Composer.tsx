import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Clock,
  Command,
  Send,
  Server,
  Square,
  Thermometer,
  Zap,
} from "lucide-react";
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format";
import type { Metrics } from "../types";

interface ComposerProps {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  metrics: Metrics;
  focusSignal: number;
}

interface CommandItem {
  name: string;
  args?: string;
  description: string;
}

const COMMANDS: CommandItem[] = [
  { name: "/new", description: "Start a new chat" },
  { name: "/mode", description: "Toggle Chat / Agent mode" },
  { name: "/model", args: "<name>", description: "Show or switch the active model" },
  { name: "/theme", args: "[accent]", description: "Cycle or set the accent color" },
  { name: "/rename", args: "<title>", description: "Rename the current chat" },
  { name: "/clear", description: "Clear the visible chat" },
  { name: "/delete", description: "Delete the current chat" },
  { name: "/help", description: "List every command" },
];

const QUICK = ["/new", "/mode", "/model", "/theme", "/help"];

export function Composer({
  onSend,
  onStop,
  streaming,
  metrics,
  focusSignal,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [selected, setSelected] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement | null>(null);

  const matches = useMemo(() => {
    const text = value.trimStart();
    if (!text.startsWith("/") || /\s/.test(text)) return [];
    return COMMANDS.filter((command) => command.name.startsWith(text.toLowerCase()));
  }, [value]);

  useEffect(() => {
    setMenuOpen(matches.length > 0);
    setSelected(0);
  }, [matches.length, value]);

  useEffect(() => {
    areaRef.current?.focus();
  }, [focusSignal]);

  useEffect(() => {
    const node = areaRef.current;
    if (!node) return;
    node.style.height = "0px";
    node.style.height = `${Math.min(node.scrollHeight, 180)}px`;
  }, [value]);

  const complete = (command: CommandItem | undefined) => {
    if (!command) return;
    setValue(command.args ? `${command.name} ` : command.name);
    setMenuOpen(false);
    requestAnimationFrame(() => {
      const node = areaRef.current;
      if (!node) return;
      node.focus();
      node.setSelectionRange(node.value.length, node.value.length);
    });
  };

  const submit = () => {
    const text = value.trim();
    if (!text) return;
    onSend(text);
    setValue("");
    setMenuOpen(false);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelected((index) => (index + 1) % matches.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelected((index) => (index - 1 + matches.length) % matches.length);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        event.preventDefault();
        complete(matches[selected]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!streaming) submit();
    }
  };

  const pct = Math.min(100, (metrics.contextUsed / Math.max(metrics.contextMax, 1)) * 100);

  return (
    <div className="card shrink-0 p-2.5">
      {!streaming && (
        <div className="mb-2 flex flex-wrap items-center gap-1 px-1">
          {QUICK.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => {
                setValue(`${name} `);
                areaRef.current?.focus();
              }}
              className="rounded-md border border-line bg-white/[0.02] px-1.5 py-0.5 font-mono text-[10.5px] text-muted transition-colors hover:border-accent/30 hover:text-accent"
            >
              {name}
            </button>
          ))}
          <span className="ml-auto hidden items-center gap-1 text-[10.5px] text-muted sm:flex">
            <Command className="h-3 w-3" />
            type <span className="font-mono text-dim">/</span> for commands
          </span>
        </div>
      )}

      <div className="relative flex items-end gap-2 rounded-xl border border-line bg-[#151515] p-2 transition-colors focus-within:border-accent/45">
        {menuOpen && (
          <div className="absolute bottom-full left-0 right-0 z-30 mb-2 overflow-hidden rounded-xl border border-line bg-[#1b1b1b] shadow-2xl">
            <div className="border-b border-line px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
              Commands
            </div>
            <ul className="max-h-56 overflow-y-auto py-1">
              {matches.map((command, index) => (
                <li key={command.name}>
                  <button
                    type="button"
                    onMouseEnter={() => setSelected(index)}
                    onClick={() => complete(command)}
                    className={cn(
                      "flex w-full items-baseline gap-2 px-3 py-1.5 text-left transition-colors",
                      index === selected ? "bg-accent/10" : "hover:bg-white/[0.03]",
                    )}
                  >
                    <span
                      className={cn(
                        "font-mono text-[12px]",
                        index === selected ? "text-accent" : "text-text",
                      )}
                    >
                      {command.name}
                    </span>
                    {command.args && (
                      <span className="font-mono text-[10.5px] text-muted">{command.args}</span>
                    )}
                    <span className="ml-auto truncate text-[10.5px] text-muted">
                      {command.description}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="border-t border-line px-3 py-1 text-[10px] text-muted">
              <span className="font-mono text-dim">↑↓</span> navigate ·{" "}
              <span className="font-mono text-dim">Tab</span> complete ·{" "}
              <span className="font-mono text-dim">Esc</span> close
            </div>
          </div>
        )}

        <textarea
          ref={areaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder={
            streaming ? "Gemma is replying…" : "Message Gemma — or type / for commands"
          }
          className="field max-h-[180px] min-h-[28px] flex-1 px-1 py-1"
          aria-label="Message"
        />

        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-danger/30 bg-danger/10 px-3 text-[12px] font-medium text-danger transition-colors hover:bg-danger/15"
          >
            <Square className="h-3 w-3 fill-current" />
            <span className="hidden sm:inline">Stop</span>
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim()}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-[12.5px] font-semibold text-[#0b0b0b] transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
          >
            <Send className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Send</span>
          </button>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1.5 overflow-x-auto px-0.5 pb-0.5 text-[10.5px]">
        <span className="pill shrink-0 gap-1.5 border-line/80 bg-white/[0.02] py-0.5 text-dim">
          <Activity className="h-3 w-3 text-accent" />
          <span className="font-mono tabular-nums">
            {formatNumber(metrics.contextUsed)}
            <span className="text-muted"> / {formatNumber(metrics.contextMax)}</span>
          </span>
          <span className="hidden h-1 w-10 overflow-hidden rounded-full bg-line sm:block">
            <span
              className="block h-full rounded-full bg-accent transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          </span>
        </span>
        <span className="pill shrink-0 gap-1 border-line/80 bg-white/[0.02] py-0.5 text-dim">
          <Zap className="h-3 w-3 text-warning" />
          <span className="font-mono tabular-nums">{metrics.tokPerSec.toFixed(1)}</span>
          <span className="text-muted">tok/s</span>
        </span>
        <span className="pill shrink-0 gap-1 border-line/80 bg-white/[0.02] py-0.5 text-dim">
          <Clock className="h-3 w-3 text-muted" />
          <span className="font-mono tabular-nums">{metrics.elapsed.toFixed(1)}s</span>
        </span>
        <span className="pill hidden shrink-0 gap-1 border-line/80 bg-white/[0.02] py-0.5 text-dim sm:inline-flex">
          <Server className="h-3 w-3 text-muted" />
          {metrics.provider}
        </span>
        <span className="pill hidden shrink-0 gap-1 border-line/80 bg-white/[0.02] py-0.5 text-dim md:inline-flex">
          <Thermometer className="h-3 w-3 text-muted" />
          <span className="font-mono tabular-nums">T {metrics.temperature.toFixed(1)}</span>
        </span>
        <span className="ml-auto hidden shrink-0 items-center gap-2 pl-2 text-[10px] text-muted xl:flex">
          <span>
            <span className="font-mono text-dim">Enter</span> send
          </span>
          <span className="text-line">·</span>
          <span>
            <span className="font-mono text-dim">Shift+Enter</span> newline
          </span>
        </span>
      </div>
    </div>
  );
}
