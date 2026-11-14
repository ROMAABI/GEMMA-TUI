import { Check, Copy, Sparkles, User } from "lucide-react";
import { cn } from "../lib/cn";
import { formatMessageTime } from "../lib/format";
import { useCopy } from "../hooks/useCopy";
import type { Message } from "../types";
import { Markdown } from "./Markdown";
import { ToolCard } from "./ToolCard";

interface MessageCardProps {
  message: Message;
  streaming: boolean;
}

export function MessageCard({ message, streaming }: MessageCardProps) {
  const { copied, copy } = useCopy();
  const isUser = message.role === "user";
  const isNote = Boolean(message.note);

  if (isNote) {
    return (
      <div className="animate-fade-up rounded-xl border border-line/70 bg-white/[0.02] px-4 py-3">
        <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-wider text-muted">
          <Sparkles className="h-3 w-3 text-accent" />
          Gemma
          <span className="text-line">·</span>
          <span className="normal-case tracking-normal">{formatMessageTime(message.createdAt)}</span>
        </div>
        <div className="text-[13.5px] leading-6 text-dim">
          <Markdown content={message.content} />
        </div>
      </div>
    );
  }

  return (
    <article
      className={cn(
        "group animate-fade-up overflow-hidden rounded-xl border bg-card shadow-card",
        isUser ? "border-line/80" : "border-line",
      )}
    >
      <header className="flex items-center gap-2.5 border-b border-line/60 px-4 py-2.5">
        <span
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border",
            isUser
              ? "border-line bg-white/[0.04] text-dim"
              : "border-accent/30 bg-accent/10 text-accent",
          )}
        >
          {isUser ? <User className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
        </span>
        <span className="text-[13px] font-semibold tracking-wide text-text">
          {message.author}
        </span>
        {!isUser && (
          <span className="hidden rounded-full border border-line px-1.5 py-px text-[9.5px] uppercase tracking-wider text-muted sm:inline">
            local
          </span>
        )}
        <span className="text-[11px] text-muted">{formatMessageTime(message.createdAt)}</span>
        {typeof message.elapsed === "number" && (
          <span className="hidden items-center gap-1 rounded-full border border-line bg-white/[0.02] px-2 py-px font-mono text-[10px] text-dim sm:inline-flex">
            ⚡ {message.elapsed.toFixed(1)}s
            {typeof message.ttft === "number" && message.ttft >= 0.5 && (
              <span className="text-muted">· first token {message.ttft.toFixed(1)}s</span>
            )}
          </span>
        )}
        {message.stopped && (
          <span className="rounded-full border border-warning/30 bg-warning/10 px-1.5 py-px text-[9.5px] uppercase tracking-wider text-warning">
            stopped
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => void copy(message.content)}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted opacity-100 transition-all hover:bg-white/5 hover:text-text focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
          aria-label="Copy message"
        >
          {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </header>

      <div className="px-4 py-3.5">
        {isUser ? (
          <p className="whitespace-pre-wrap text-[15px] leading-7 text-text">{message.content}</p>
        ) : message.content ? (
          <Markdown content={message.content} />
        ) : (
          <div className="flex items-center gap-2 text-[13px] text-muted">
            <span className="flex gap-1">
              <span className="h-1.5 w-1.5 animate-blink rounded-full bg-accent" />
              <span
                className="h-1.5 w-1.5 animate-blink rounded-full bg-accent"
                style={{ animationDelay: "0.2s" }}
              />
              <span
                className="h-1.5 w-1.5 animate-blink rounded-full bg-accent"
                style={{ animationDelay: "0.4s" }}
              />
            </span>
            Gemma is thinking…
          </div>
        )}

        {streaming && message.content && (
          <span className="ml-0.5 inline-block h-4 w-[7px] translate-y-0.5 animate-blink rounded-sm bg-accent align-baseline" />
        )}

        {message.tools && message.tools.length > 0 && (
          <div className="mt-3 space-y-1.5 border-t border-line/60 pt-3">
            {message.tools.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
