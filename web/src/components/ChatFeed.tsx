import { useEffect, useRef, useState } from "react";
import { ArrowDown, MessageSquare, Sparkles } from "lucide-react";
import { cn } from "../lib/cn";
import { shortSessionId, timeAgo } from "../lib/format";
import { WELCOME_SUGGESTIONS } from "../data/seed";
import type { Session } from "../types";
import { MessageCard } from "./MessageCard";

interface ChatFeedProps {
  session: Session;
  streaming: boolean;
  streamingId: string | null;
  onSend: (text: string) => void;
}

export function ChatFeed({ session, streaming, streamingId, onSend }: ChatFeedProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);

  const last = session.messages[session.messages.length - 1];
  const lastLength = last?.content.length ?? 0;

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !pinned) return;
    node.scrollTop = node.scrollHeight;
  }, [pinned, session.id, session.messages.length, lastLength]);

  const jumpToBottom = () => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    setPinned(true);
  };

  return (
    <section className="card relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line/70 px-4 py-2.5">
        <MessageSquare className="h-3.5 w-3.5 shrink-0 text-accent" />
        <h2 className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-text">
          {session.title}
        </h2>
        <span className="hidden font-mono text-[10.5px] text-muted sm:inline">
          {shortSessionId(session.id)}
        </span>
        <span className="text-line">·</span>
        <span className="hidden text-[10.5px] text-muted sm:inline">
          {session.messages.length} messages
        </span>
        <span className="text-line">·</span>
        <span className="text-[10.5px] text-muted">{timeAgo(session.updatedAt)}</span>
      </div>

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const node = event.currentTarget;
          const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
          setPinned(distance < 72);
        }}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3 sm:px-4 sm:py-4"
      >
        {session.messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center py-10 text-center">
            <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-accent/25 bg-accent/10 text-accent shadow-glow">
              <Sparkles className="h-5 w-5" />
            </span>
            <h3 className="text-lg font-semibold text-text">What are we building today?</h3>
            <p className="mt-1 max-w-sm text-[13px] leading-6 text-muted">
              Ask anything, or start with one of these. Type{" "}
              <span className="font-mono text-dim">/</span> for commands.
            </p>
            <div className="mt-5 grid w-full gap-2 sm:grid-cols-3">
              {WELCOME_SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion.title}
                  type="button"
                  onClick={() => onSend(suggestion.prompt)}
                  className="rounded-xl border border-line bg-[#161616] p-3 text-left transition-colors hover:border-accent/35 hover:bg-accent/[0.04]"
                >
                  <span className="block text-[12px] font-medium text-text">{suggestion.title}</span>
                  <span className="mt-1 block text-[11px] leading-5 text-muted">
                    {suggestion.prompt}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto flex max-w-4xl flex-col gap-3">
            {session.messages.map((message) => (
              <MessageCard
                key={message.id}
                message={message}
                streaming={streaming && message.id === streamingId}
              />
            ))}
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={jumpToBottom}
        className={cn(
          "absolute bottom-3 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-[#1e1e1e]/95 px-3 py-1.5 text-[11px] text-dim shadow-lg backdrop-blur transition-all hover:text-text",
          pinned ? "pointer-events-none translate-y-2 opacity-0" : "opacity-100",
        )}
      >
        <ArrowDown className="h-3 w-3" />
        Jump to latest
      </button>
    </section>
  );
}
