export interface ServerInfo {
  app: "gemma";
  version: string;
  model: string;
  mode: "chat" | "agent";
  workspace: string;
  provider: string;
  temperature: number;
  context_max: number;
  dist: boolean;
}

export interface ServerSession {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
}

export interface ServerMessage {
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface ServerSessionDetail {
  id: string;
  title: string;
  updated_at: string;
  messages: ServerMessage[];
}

export interface ServerDone {
  elapsed: number;
  ttft: number;
  tokens: number;
}

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { ...JSON_HEADERS, ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed (${response.status})`);
  }
  return (await response.json()) as T;
}

/** Detect the Gemma server that serves this page (OpenCode-style pairing). */
export async function detectServer(): Promise<ServerInfo | null> {
  try {
    const response = await fetch("/api/info", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) return null;
    const type = response.headers.get("content-type") ?? "";
    if (!type.includes("application/json")) return null;
    const info = (await response.json()) as ServerInfo;
    return info?.app === "gemma" ? info : null;
  } catch {
    return null;
  }
}

export async function fetchSessions(): Promise<ServerSession[]> {
  const data = await json<{ sessions: ServerSession[] }>("/api/sessions");
  return data.sessions ?? [];
}

export function fetchSession(id: string): Promise<ServerSessionDetail> {
  return json<ServerSessionDetail>(`/api/sessions/${encodeURIComponent(id)}`);
}

export async function createSession(): Promise<string> {
  const data = await json<{ id: string }>("/api/sessions", { method: "POST" });
  return data.id;
}

export async function renameSession(id: string, title: string): Promise<void> {
  await json(`/api/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteSession(id: string): Promise<void> {
  await json(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function clearSession(id: string): Promise<void> {
  await json(`/api/sessions/${encodeURIComponent(id)}/clear`, { method: "POST" });
}

export interface ServerStreamOptions {
  signal?: AbortSignal;
  onToken: (token: string) => void;
  onDone?: (done: ServerDone) => void;
}

/** Stream a reply from the Gemma server (`POST /api/chat`). */
export async function streamServerChat(
  sessionId: string,
  message: string,
  opts: ServerStreamOptions,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal: opts.signal,
  });

  if (!response.ok || !response.body) {
    if (response.status === 401) {
      throw new Error("Pairing required. Run /web in the Gemma TUI for a new link.");
    }
    throw new Error(`Gemma server request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handle = (payload: string) => {
    if (payload === "[DONE]") return;
    let event: { type?: string; content?: string; message?: string } & Partial<ServerDone>;
    try {
      event = JSON.parse(payload);
    } catch {
      return;
    }
    if (event.type === "token" && typeof event.content === "string") {
      opts.onToken(event.content);
    } else if (event.type === "done") {
      opts.onDone?.({
        elapsed: Number(event.elapsed ?? 0),
        ttft: Number(event.ttft ?? 0),
        tokens: Number(event.tokens ?? 0),
      });
    } else if (event.type === "error") {
      throw new Error(event.message || "The model returned an error.");
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      for (const line of event.split("\n")) {
        if (line.startsWith("data:")) handle(line.slice(5).trim());
      }
    }
  }
}
