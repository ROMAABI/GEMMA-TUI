import type { ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./CodeBlock";

interface MarkdownProps {
  content: string;
}

function Code({ className, children }: ComponentPropsWithoutRef<"code"> & { children?: ReactNode }) {
  const text = String(children ?? "").replace(/\n$/, "");
  const match = /language-([\w-]+)/.exec(className ?? "");
  const isBlock = Boolean(match) || text.includes("\n");

  if (!isBlock) {
    return (
      <code className="rounded-md border border-line/70 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[0.86em] text-[#e6edf3]">
        {children}
      </code>
    );
  }

  return <CodeBlock code={text} language={match?.[1]} />;
}

export function Markdown({ content }: MarkdownProps) {
  return (
    <div className="prose-gemma">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <>{children}</>,
          code: Code,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
