/**
 * Client for the local Gemma server (`core/webui.py`).
 *
 * The TUI is a client of the same server that serves the web UI: it pairs once
 * with a one-time link, then talks JSON + SSE over the authenticated origin.
 */

import type { Sampling } from "./prefs"

export interface AttachmentDescriptor {
  id: string
  name: string
  kind: "image" | "video" | "text" | "pdf"
  size: number
  mime?: string
  stored_path: string
  preview?: string | null
  text?: string
  duration?: number
  frames?: number
  note?: string
  audio_skipped?: boolean
  tokens_est?: number
}

export interface AttachmentCaps {
  vision: boolean
  ffmpeg: boolean
  chafa: boolean
  pdftotext: boolean
  max_attachments: number
  max_file_bytes: number
}

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
  vision?: boolean
  attachments?: AttachmentCaps
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

export interface ResearchPrepare {
  search: boolean
  reason?: string
  query?: string
  rewritten?: string
  intent?: string
  entity?: string
  context?: string
  elapsed?: number
  /** Top results, for showing sources under the answer. */
  results?: ResearchResult[]
  /** Present (with results: []) when every query failed. */
  healthy?: boolean
  detail?: string
}

export interface ResearchStatus {
  healthy: boolean
  detail: string
  url: string
  deep_read: boolean
}

export interface ResearchResult {
  title?: string
  url?: string
  engine?: string
  snippet?: string
  /** Published date (YYYY-MM-DD) when the engine reports one. */
  date?: string
  /** Page text fetched during the deep-read phase. */
  excerpt?: string
}

export interface ResearchTest {
  ok: boolean
  query: string
  error?: string
  results: ResearchResult[]
}

export interface CompactResult {
  ok: boolean
  reason?: string
  removed?: number
  before_tokens?: number
  after_tokens?: number
  summary?: string
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
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) throw new Error("The server returned no stream body.")
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  if (signal) {
    if (signal.aborted) {
      await reader.cancel()
      throw new DOMException("The user aborted a request.", "AbortError")
    }
    signal.addEventListener(
      "abort",
      () => {
        void reader.cancel().catch(() => {})
      },
      { once: true },
    )
  }

  while (true) {
    if (signal?.aborted) {
      throw new DOMException("The user aborted a request.", "AbortError")
    }
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

  private async json<T>(path: string, init: RequestInit = {}, timeoutMs?: number): Promise<T> {
    const response = await fetch(`${this.base}${path}`, {
      ...init,
      signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : init.signal,
      headers: this.headers((init.headers as Record<string, string>) ?? {}),
    })
    if (response.status === 401) throw new PairingError("Pairing required.")
    if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path} failed (${response.status})`)
    return (await response.json()) as T
  }

  /** Capabilities of the attachment pipeline. */
  attachmentCapabilities(): Promise<AttachmentCaps> {
    return this.json("/api/attachments/capabilities")
  }

  /** Ask the server to resize/extract one local file into a stored descriptor. */
  async prepareAttachment(
    path: string,
    sessionId: string,
  ): Promise<{ ok: boolean; attachment?: AttachmentDescriptor; error?: string }> {
    const response = await fetch(`${this.base}/api/attachments/prepare`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({ path, session_id: sessionId }),
    })
    return (await response.json()) as {
      ok: boolean
      attachment?: AttachmentDescriptor
      error?: string
    }
  }

  /** Path completions for /attach <prefix>. */
  suggestPaths(query: string): Promise<{ paths: string[] }> {
    return this.json(`/api/attachments/suggest?q=${encodeURIComponent(query)}`)
  }

  info(): Promise<ServerInfo> {
    return this.json<ServerInfo>("/api/info")
  }

  /** Tail of the server log, shown in the in-TUI /logs panel. */
  logs(): Promise<{ path: string; text: string }> {
    return this.json("/api/logs")
  }

  /** Liveness probe (the header connection dot polls this). */
  health(): Promise<{ status: string; app: string }> {
    return this.json("/health")
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

  /** Judge + SearXNG pipeline for one user message (server-side). */
  /** Judge the message and run the SearXNG search (phase 1; no page reads). */
  researchPrepare(
    message: string,
    history: string[] = [],
    deepRead = true,
  ): Promise<ResearchPrepare> {
    return this.json(
      "/api/research/prepare",
      { method: "POST", body: JSON.stringify({ message, history, deep_read: deepRead }) },
      30_000,
    )
  }

  /** Phase 2: fetch the top result pages in parallel and build the context. */
  researchRead(
    query: string,
    results: ResearchResult[],
  ): Promise<{ ok: boolean; query: string; context?: string; results: ResearchResult[]; error?: string }> {
    return this.json(
      "/api/research/read",
      { method: "POST", body: JSON.stringify({ query, results }) },
      30_000,
    )
  }

  /** SearXNG connectivity snapshot. */
  researchStatus(): Promise<ResearchStatus> {
    return this.json("/api/research/status")
  }

  /** Run one real search (top 3 results) to verify the engine works. */
  researchTest(query: string): Promise<ResearchTest> {
    return this.json("/api/research/test", { method: "POST", body: JSON.stringify({ query }) })
  }

  /** Point the pipeline at another SearXNG instance. */
  researchConfig(url: string): Promise<{ ok: boolean; url: string; healthy: boolean; detail: string }> {
    return this.json("/api/research/config", { method: "POST", body: JSON.stringify({ url }) })
  }

  /** Compact a session into a checkpoint (manual /compact). */
  compactSession(id: string, instructions?: string): Promise<CompactResult> {
    return this.json("/api/compact", {
      method: "POST",
      body: JSON.stringify(instructions ? { session_id: id, instructions } : { session_id: id }),
    })
  }

  /** Write a starter AGENTS.md into the workspace (/init). */
  initWorkspace(): Promise<{ ok: boolean; path: string; reason?: string }> {
    return this.json("/api/init", { method: "POST" })
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
    context?: string,
    attachments?: AttachmentDescriptor[],
    sampling?: Sampling,
    system?: string,
  ): Promise<void> {
    const body: Record<string, unknown> = { session_id: sessionId, message }
    if (context) body.context = context
    if (attachments?.length) body.attachments = attachments
    if (sampling) body.sampling = sampling
    if (system) body.system = system
    const response = await fetch(`${this.base}/api/chat`, {
      method: "POST",
      headers: this.headers({ Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal,
    })
    if (response.status === 401) throw new PairingError("Pairing required.")
    if (!response.ok) throw new Error(`chat failed (${response.status})`)

    let failure: Error | null = null
    await readSSE(
      response,
      (payload) => {
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
      },
      signal,
    )
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
    context?: string,
    attachments?: AttachmentDescriptor[],
    sampling?: Sampling,
    system?: string,
  ): Promise<void> {
    const body: Record<string, unknown> = { session_id: sessionId, message }
    if (context) body.context = context
    if (attachments?.length) body.attachments = attachments
    if (sampling) body.sampling = sampling
    if (system) body.system = system
    const response = await fetch(`${this.base}/api/agent`, {
      method: "POST",
      headers: this.headers({ Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal,
    })
    if (response.status === 401) throw new PairingError("Pairing required.")
    if (!response.ok) throw new Error(`agent failed (${response.status})`)

    await readSSE(
      response,
      (payload) => {
        if (payload === "[DONE]") return
        let event: AgentEvent
        try {
          event = JSON.parse(payload) as AgentEvent
        } catch {
          return
        }
        if (event.type === "start" && event.session_id) handlers.onStart?.(event.session_id)
        handlers.onEvent(event)
      },
      signal,
    )
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
