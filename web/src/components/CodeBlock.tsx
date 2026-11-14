import { Check, Copy } from "lucide-react";
import { useCopy } from "../hooks/useCopy";

interface CodeBlockProps {
  code: string;
  language?: string;
}

export function CodeBlock({ code, language }: CodeBlockProps) {
  const { copied, copy } = useCopy();

  return (
    <div className="group/code my-3 overflow-hidden rounded-lg border border-line bg-[#141414]">
      <div className="flex items-center justify-between border-b border-line/80 bg-white/[0.02] px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-muted">
          {language ?? "code"}
        </span>
        <button
          type="button"
          onClick={() => void copy(code)}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:bg-white/5 hover:text-text"
        >
          {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-[13px] leading-6">
        <code className="font-mono text-[#d6deeb]">{code}</code>
      </pre>
    </div>
  );
}
