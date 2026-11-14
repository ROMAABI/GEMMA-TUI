/**
 * Client for the local Gemma server (`core/webui.py`).
 *
 * The TUI is a client of the same server that serves the web UI: it pairs once
 * with a one-time link, then talks JSON + SSE over the authenticated origin.
 */

export interface ServerInfo {
  app: string
  version: string
  model: string
  mode: "chat" | "agent"
  workspace: string
  provider: string
  provider_mode?: string
  base_url?: string
  permission_mode?: string
  temperature: number
  context_max: number
  max_steps?: number
}

export interface SessionMeta {
  id: string
  title: string
  updated_at: string
  message_count: number
}

export interface SessionDetail {
  id: string
  title: string
  updated_at: string
  messages: ServerMessage[]
}

export interface ServerMessage {
  role: "user" | "assistant" | "system" | "tool"
  content: string
  created_at: string
  tool_call_id?: string | null
  name?: string | null
  metadata?: Record<string, unknown>
}

export interface ModelEntry {
  name: string
  source: "current" | "loaded" | "local" | "remote" | string
  active: boolean
}

export interface ModelList {
  active: string
  models: ModelEntry[]
}

export interface Provider {
  id: string
  name: string
  kind: "local" | "openai" | string
  base_url: string
  is_default: boolean
  default_model: string
  models: ModelEntry[]
}

export interface ProviderCatalog {
  default: string
  active: { provider: string; model: string }
  providers: Provider[]
}

export interface CommandDef {
  name: string
  description: string
}

export interface PermissionConfig {
  mode: string
  rules: string[]
}

export interface DoneStats {
  elapsed: number
  ttft: number
  tokens: number
}

/** One event from the agent SSE stream (`/api/agent`). */
export interface AgentEvent {
  type: string
  session_id?: string
  run_id?: string
  content?: unknown
  message?: string
  /** permission_request fields */
  id?: string
  action?: string
  tool?: string
  resources?: string[]
  summary?: string
  reason?: string
  args?: Record<string, unknown>
}

export class PairingError extends Error {}

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" }

/** Split an SSE byte stream into `data:` payloads. */
async function readSSE(
  response: Response,
  onPayload: (payload: string) => void,
): Promise<void> {
  if (!response.body) throw new Error("The server returned no stream body.")
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split("\n\n")
    buffer = events.pop() ?? ""
    for (const event of events) {
      for (const line of event.split("\n")) {
        // Comments are keep-alives.
        if (line.startsWith("data:")) onPayload(line.slice(5).trim())
      }
    }
  }
}

export class GemmaClient {
  readonly base: string
  private cookie: string | null

  constructor(base: string, cookie: string | null = null) {
    this.base = base.replace(/\/+$/, "")
    this.cookie = cookie
  }

  /** Pair with a one-time link and keep the session cookie. */
  static async pair(base: string, pairUrl?: string): Promise<GemmaClient> {
    const client = new GemmaClient(base)
    if (!pairUrl) return client

    const response = await fetch(pairUrl, { redirect: "manual" })
    if (response.status === 403) {
      throw new PairingError(
        "That pairing link is expired or already used. Restart `gemma` for a fresh one.",
      )
    }
    const setCookies =
      (response.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie?.() ??
      [response.headers.get("set-cookie") ?? ""]
    for (const header of setCookies) {
      const match = /(?:^|,\s*)gemma_web=([^;]+)/.exec(header)
      if (match) client.cookie = `gemma_web=${match[1]}`
    }
    if (!client.cookie) throw new PairingError("The server did not set a session cookie.")
    return client
  }

  get paired(): boolean {
    return this.cookie !== null
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return {
      ...JSON_HEADERS,
      ...extra,
      ...(this.cookie ? { Cookie: this.cookie } : {}),
    }
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.base}${path}`, {
      ...init,
      headers: this.headers((init.headers as Record<string, string>) ?? {}),
    })
    if (response.status === 401) throw new PairingError("Pairing required.")
    if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path} failed (${response.status})`)
    return (await response.json()) as T
  }

  info(): Promise<ServerInfo> {
    return this.json<ServerInfo>("/api/info")
  }

  sessions(): Promise<SessionMeta[]> {
    return this.json<{ sessions: SessionMeta[] }>("/api/sessions").then((data) => data.sessions ?? [])
  }

  session(id: string): Promise<SessionDetail> {
    return this.json<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`)
  }

  async createSession(): Promise<string> {
    const data = await this.json<{ id: string }>("/api/sessions", { method: "POST" })
    return data.id
  }

  renameSession(id: string, title: string): Promise<unknown> {
    return this.json(`/api/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    })
  }

  deleteSession(id: string): Promise<unknown> {
    return this.json(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" })
  }

  clearSession(id: string): Promise<unknown> {
    return this.json(`/api/sessions/${encodeURIComponent(id)}/clear`, { method: "POST" })
  }

  models(): Promise<ModelList> {
    return this.json<ModelList>("/api/models")
  }

  providers(): Promise<ProviderCatalog> {
    return this.json<ProviderCatalog>("/api/providers")
  }

  switchModel(name: string, provider?: string): Promise<unknown> {
    return this.json("/api/models", {
      method: "POST",
      body: JSON.stringify(provider ? { provider, name } : { name }),
    })
  }

  setMode(mode: "chat" | "agent"): Promise<unknown> {
    return this.json("/api/mode", { method: "POST", body: JSON.stringify({ mode }) })
  }

  commands(): Promise<CommandDef[]> {
    return this.json<{ commands: CommandDef[] }>("/api/commands").then((data) => data.commands ?? [])
  }

  /** Mint a fresh pairing link for the browser (the server keeps one at a time). */
  async webLink(): Promise<string> {
    const data = await this.json<{ pair: string }>("/api/pair", { method: "POST" })
    return data.pair.startsWith("http") ? data.pair : `${this.base}${data.pair}`
  }

  /** Answer a pending permission prompt. */
  respondPermission(id: string, decision: "allow" | "always" | "deny"): Promise<unknown> {
    return this.json(`/api/permissions/${encodeURIComponent(id)}`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    })
  }

  /** Read the persisted permission policy (mode + user rules). */
  permissionConfig(): Promise<PermissionConfig> {
    return this.json<PermissionConfig>("/api/permissions/config")
  }

  /** Update the permission policy: mode, one rule, or a full reset. */
  setPermissionConfig(patch: {
    mode?: string
    add?: string
    effect?: "allow" | "deny" | "ask"
    action?: string
    resource?: string
    reset?: boolean
  }): Promise<PermissionConfig> {
    return this.json<PermissionConfig>("/api/permissions/config", {
      method: "POST",
      body: JSON.stringify(patch),
    })
  }

  /** Choose native tool calling, XML fallback, or automatic. */
  setProviderMode(mode: "auto" | "native" | "prompt"): Promise<unknown> {
    return this.json("/api/provider_mode", { method: "POST", body: JSON.stringify({ mode }) })
  }

  /** Set the context token limit (server clamps to 2,000..1,000,000). */
  setContextLimit(limit: number): Promise<{ ok: boolean; context_max: number }> {
    return this.json("/api/context", { method: "POST", body: JSON.stringify({ limit }) })
  }

  /** Agent settings: workspace directory and max autonomous steps. */
  setAgentSettings(patch: { workspace?: string; max_steps?: number }): Promise<unknown> {
    return this.json("/api/agent/settings", { method: "POST", body: JSON.stringify(patch) })
  }

  /** MCP manager status snapshot (may not be started before the first agent run). */
  mcpStatus(): Promise<{ enabled: boolean; status: string }> {
    return this.json("/api/mcp/status")
  }

  /** Skills list summary (empty string when no skills are installed). */
  skillsSummary(): Promise<{ enabled: boolean; summary: string }> {
    return this.json("/api/skills")
  }

  /** LSP manager status snapshot. */
  lspStatus(): Promise<{ enabled: boolean; status: string }> {
    return this.json("/api/lsp/status")
  }

  /** Drive MCP (start|reload|stop), skills (list|reload) or LSP (status|stop). */
  serviceAction(
    service: "mcp" | "skills" | "lsp",
    action: string,
  ): Promise<{ ok: boolean; service: string; action: string; status: string }> {
    return this.json(`/api/${service}`, { method: "POST", body: JSON.stringify({ action }) })
  }

  abortRun(runId: string): Promise<unknown> {
    return this.json("/api/agent/abort", { method: "POST", body: JSON.stringify({ run_id: runId }) })
  }

  /** Stream a chat reply. Resolves when the server closes the stream. */
  async streamChat(
    sessionId: string,
    message: string,
    handlers: {
      onStart?: (sessionId: string) => void
      onToken: (token: string) => void
      onDone?: (stats: DoneStats) => void
    },
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(`${this.base}/api/chat`, {
      method: "POST",
      headers: this.headers({ Accept: "text/event-stream" }),
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    })
    if (response.status === 401) throw new PairingError("Pairing required.")
    if (!response.ok) throw new Error(`chat failed (${response.status})`)

    let failure: Error | null = null
    await readSSE(response, (payload) => {
      if (payload === "[DONE]") return
      let event: {
        type?: string
        content?: string
        message?: string
        session_id?: string
        elapsed?: number
        ttft?: number
        tokens?: number
      }
      try {
        event = JSON.parse(payload)
      } catch {
        return
      }
      if (event.type === "start" && event.session_id) handlers.onStart?.(event.session_id)
      else if (event.type === "token" && typeof event.content === "string") handlers.onToken(event.content)
      else if (event.type === "done") {
        handlers.onDone?.({
          elapsed: Number(event.elapsed ?? 0),
          ttft: Number(event.ttft ?? 0),
          tokens: Number(event.tokens ?? 0),
        })
      } else if (event.type === "error") {
        failure = new Error(event.message || "The model returned an error.")
      }
    })
    if (failure) throw failure
  }

  /** Stream an agent turn: text, reasoning, tool calls and permission prompts. */
  async streamAgent(
    sessionId: string,
    message: string,
    handlers: {
      onStart?: (sessionId: string) => void
      onEvent: (event: AgentEvent) => void
    },
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(`${this.base}/api/agent`, {
      method: "POST",
      headers: this.headers({ Accept: "text/event-stream" }),
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    })
    if (response.status === 401) throw new PairingError("Pairing required.")
    if (!response.ok) throw new Error(`agent failed (${response.status})`)

    await readSSE(response, (payload) => {
      if (payload === "[DONE]") return
      let event: AgentEvent
      try {
        event = JSON.parse(payload) as AgentEvent
      } catch {
        return
      }
      if (event.type === "start" && event.session_id) handlers.onStart?.(event.session_id)
      handlers.onEvent(event)
    })
  }
}

/** Resolve the server address from the environment. */
export function serverAddress(): { base: string; pairUrl?: string } {
  const base = (process.env.GEMMA_URL || process.env.GEMMA_SERVER_URL || "http://127.0.0.1:47317").replace(
    /\/+$/,
    "",
  )
  return { base, pairUrl: process.env.GEMMA_PAIR_URL || undefined }
}
