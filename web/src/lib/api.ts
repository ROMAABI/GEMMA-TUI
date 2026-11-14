import type { Message, ToolStep } from "../types";

const BASE_URL = (import.meta.env.VITE_GEMMA_API_URL ?? "").replace(/\/$/, "");

const SYSTEM_PROMPT =
  "You are Gemma, a concise, helpful local AI assistant. Reply directly, without " +
  "exposing reasoning steps. Use Markdown for code and lists.";

export interface StreamOptions {
  model: string;
  signal?: AbortSignal;
  onToken: (token: string) => void;
  onTools?: (tools: ToolStep[]) => void;
}

const GREETING =
  /^(hi|hey|hello|yo|sup|hiya|good (morning|afternoon|evening))\b[^.!?]{0,24}$/i;

function id(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

function mockAnswer(prompt: string): { text: string; tools?: ToolStep[] } {
  const text = prompt.trim();

  if (GREETING.test(text)) {
    return { text: "Hi! How can I help you today? 😊" };
  }

  if (/\b(test|pytest|refactor|parser|lint|typecheck)\b/i.test(text)) {
    return {
      text:
        "I'd take this in three small steps:\n\n" +
        "1. **Read the module** and map the current public API.\n" +
        "2. **Extract the pure pieces** so they can be tested without I/O.\n" +
        "3. **Run the suite** after each move and keep the diff reviewable.\n\n" +
        "```python\n" +
        "def parse(source: str) -> Node:\n" +
        "    tokens = tokenize(source)\n" +
        "    return Parser(tokens).document()\n" +
        "```\n\n" +
        "I ran the tests: **12 passed in 0.41s**. Nothing else needs to change.",
      tools: [
        {
          id: id("tool"),
          step: 1,
          tool: "bash",
          target: "pytest -q",
          status: "done",
          output: "12 passed in 0.41s",
        },
      ],
    };
  }

  if (/\b(create|write|make|add|file|script|component|function)\b/i.test(text)) {
    return {
      text:
        "Here's a compact implementation you can drop in:\n\n" +
        "```ts\n" +
        "export function chunk<T>(items: T[], size: number): T[][] {\n" +
        "  if (size <= 0) throw new Error(\"size must be > 0\");\n" +
        "  const out: T[][] = [];\n" +
        "  for (let i = 0; i < items.length; i += size) {\n" +
        "    out.push(items.slice(i, i + size));\n" +
        "  }\n" +
        "  return out;\n" +
        "}\n" +
        "```\n\n" +
        "It's pure, handles empty input, and fails loudly on a bad size.",
      tools: [
        {
          id: id("tool"),
          step: 1,
          tool: "write",
          target: "src/lib/chunk.ts",
          status: "done",
          output: "Wrote 12 lines",
        },
      ],
    };
  }

  if (/\b(explain|what is|how does|why|concept)\b/i.test(text)) {
    return {
      text:
        "**Attention** lets every token look at every other token and decide how much " +
        "each one matters.\n\n" +
        "- Each token becomes a **query**, a **key**, and a **value**.\n" +
        "- Queries are compared with keys to produce weights.\n" +
        "- The weights blend the values into a new representation.\n\n" +
        "Stack that in parallel heads and you get a block that mixes context at every " +
        "position in one step, which is why transformers scale so well.",
    };
  }

  return {
    text:
      `Here's how I'd approach that:\n\n` +
      `- Break **${text.length > 60 ? text.slice(0, 60) + "…" : text}** into the smallest ` +
      `verifiable step.\n` +
      `- Confirm the current behavior before changing anything.\n` +
      `- Make one focused change, then re-run the relevant check.\n\n` +
      `Tell me which step you want me to start with and I'll take it from there.`,
  };
}

function splitTokens(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [text];
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

async function mockStream(messages: Message[], opts: StreamOptions): Promise<void> {
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const { text, tools } = mockAnswer(lastUser?.content ?? "");

  await sleep(420, opts.signal);
  if (tools && tools.length > 0) {
    opts.onTools?.(tools);
    await sleep(650, opts.signal);
  }

  for (const token of splitTokens(text)) {
    if (opts.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    opts.onToken(token);
    await sleep(18 + Math.random() * 18, opts.signal);
  }
}

async function realStream(messages: Message[], opts: StreamOptions): Promise<void> {
  const payload = {
    model: opts.model,
    stream: true,
    temperature: 0.7,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      ...messages
        .filter((m) => !m.note)
        .map((m) => ({ role: m.role, content: m.content })),
    ],
  };

  const response = await fetch(`${BASE_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: opts.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Model request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          const json = JSON.parse(data);
          const delta = json.choices?.[0]?.delta?.content;
          if (typeof delta === "string" && delta.length > 0) opts.onToken(delta);
        } catch {
          // Ignore keep-alives and partial JSON.
        }
      }
    }
  }
}

export async function streamReply(messages: Message[], opts: StreamOptions): Promise<void> {
  if (BASE_URL) {
    await realStream(messages, opts);
    return;
  }
  await mockStream(messages, opts);
}

export async function checkHealth(): Promise<boolean> {
  if (!BASE_URL) return true;
  try {
    const response = await fetch(`${BASE_URL}/health`, { signal: AbortSignal.timeout(2000) });
    return response.ok;
  } catch {
    return false;
  }
}

export const hasLiveBackend = Boolean(BASE_URL);
