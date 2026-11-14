import { useState } from "react";
import { Check, ChevronRight, CircleAlert, Loader2, Terminal } from "lucide-react";
import { cn } from "../lib/cn";
import type { ToolStep } from "../types";

interface ToolCardProps {
  tool: ToolStep;
}

const STATUS_LABEL: Record<ToolStep["status"], string> = {
  running: "running",
  done: "done",
  error: "failed",
};

export function ToolCard({ tool }: ToolCardProps) {
  const [open, setOpen] = useState(false);
  const hasOutput = Boolean(tool.output);

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-[#161616]">
      <button
        type="button"
        onClick={() => hasOutput && setOpen((value) => !value)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] transition-colors",
          hasOutput && "hover:bg-white/[0.03]",
        )}
        aria-expanded={hasOutput ? open : undefined}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted transition-transform",
            open && "rotate-90",
            !hasOutput && "opacity-30",
          )}
        />
        <Terminal className="h-3.5 w-3.5 shrink-0 text-accent" />
        <span className="shrink-0 font-mono font-medium text-text">{tool.tool}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-muted">{tool.target}</span>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
            tool.status === "running" && "border-accent/30 bg-accent/10 text-accent",
            tool.status === "done" && "border-success/30 bg-success/10 text-success",
            tool.status === "error" && "border-danger/30 bg-danger/10 text-danger",
          )}
        >
          {tool.status === "running" && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
          {tool.status === "done" && <Check className="h-2.5 w-2.5" />}
          {tool.status === "error" && <CircleAlert className="h-2.5 w-2.5" />}
          {STATUS_LABEL[tool.status]}
        </span>
      </button>
      {open && hasOutput && (
        <pre className="overflow-x-auto border-t border-line bg-[#111111] px-3 py-2 font-mono text-[11.5px] leading-5 text-dim">
          {tool.output}
        </pre>
      )}
    </div>
  );
}
