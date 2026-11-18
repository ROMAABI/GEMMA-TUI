/** Chat state for the TUI: sessions, streaming and metrics over the server API. */

import { createMemo, createSignal } from "solid-js"
import {
  GemmaClient,
  PairingError,
  type AgentEvent,
  type CommandDef,
  type DoneStats,
  type ModelList,
  type PermissionConfig,
  type ProviderCatalog,
  type ServerInfo,
  type ServerMessage,
} from "./api"
import { displayModelName, estimateTokens } from "./format"

export type ToolStatus = "running" | "done" | "error"

export type Part =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | {
      kind: "tool"
      id: string
      name: string
      args: Record<string, unknown>
      status: ToolStatus
      result?: string
      startedAt: number
      finishedAt?: number
    }

export interface Message {
  id: string
  role: "user" | "assistant"
  /** Text-only projection, used for copy and token estimates. */
  content: string
  parts: Part[]
  createdAt: string
  elapsed?: number
  ttft?: number
  /** Generation tokens reported by the provider, when available. */
  tokens?: number
  /** Real generation time reported by the provider, for a consistent tok/s. */
  genMs?: number
  /** Total tool execution time in this turn. */
  toolMs?: number
  stopped?: boolean
  error?: string
  /** Web-search state for this turn: status line while running, sources after. */
  search?: SearchState
}

export interface SearchState {
  status: "searching" | "reading" | "ok" | "failed"
  query?: string
  count?: number
  sources?: Array<{ title: string; domain: string; date?: string }>
  error?: string
}

export interface Session {
  id: string
  title: string
  updatedAt: string
  messages: Message[]
}

export interface Metrics {
  contextUsed: number
  contextMax: number
  tokens: number
  elapsed: number
  tokPerSec: number
  provider: string
  temperature: number
  websearch: boolean
}

// Greetings never need a web-search decision; skipping it avoids a pointless
// round-trip. Ported from tui/app.py's _SMALLTALK_RE.
const SMALLTALK_RE =
  /^(hi|hey|hello|yo|sup|hiya)(\s+(gemma|there|bot|assistant|friend))?[\s!.,?]*$|^(thanks|thank you|thx|ok|okay|good (morning|afternoon|evening|night)|how are you|how's it going|what'?s up|bye|goodbye)[\s!.,?]*$/i

const MEMORY_PREFIX = "(from model memory, may be outdated) "

/** Mark a reply that was answered without live search results. */
function markMemoryFallback(message: Message): Message {
  const text = MEMORY_PREFIX + message.content
  const parts = message.parts.map((part, index) =>
    index === 0 && part.kind === "text" ? { ...part, text: MEMORY_PREFIX + part.text } : part,
  )
  if (!parts.some((part) => part.kind === "text")) {
    parts.unshift({ kind: "text", text: MEMORY_PREFIX })
  }
  return { ...message, content: text, parts }
}

let counter = 0

function nextId(prefix: string): string {
  counter += 1
  return `${prefix}${Date.now().toString(36)}${counter.toString(36)}`
}

/** Local placeholder sessions have no server row until the first send. */
const LOCAL_SESSION_PREFIX = "local-"
const isLocalSession = (id: string): boolean => id.startsWith(LOCAL_SESSION_PREFIX)

export function textOf(parts: Part[]): string {
  return parts
    .filter((part): part is Extract<Part, { kind: "text" }> => part.kind === "text")
    .map((part) => part.text)
    .join("")
}

function appendChunk(parts: Part[], kind: "text" | "reasoning", chunk: string): Part[] {
  const last = parts[parts.length - 1]
  if (last && last.kind === kind) {
    return [...parts.slice(0, -1), { kind, text: last.text + chunk }]
  }
  return [...parts, { kind, text: chunk }]
}

function parseArgs(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>
  if (typeof raw === "string" && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {}
    } catch {
      return { input: raw }
    }
  }
  return {}
}

/** Rebuild parts from the server's persisted messages (tool calls included). */
function fromServer(messages: ServerMessage[]): Message[] {
  const out: Message[] = []
  for (const item of messages) {
    if (item.role === "system") continue

    if (item.role === "tool") {
      const callId = item.tool_call_id ?? ""
      for (let index = out.length - 1; index >= 0; index -= 1) {
        const part = out[index]!.parts.find(
          (candidate) => candidate.kind === "tool" && candidate.id === callId,
        )
        if (part && part.kind === "tool") {
          part.result = item.content
          part.status = item.metadata?.ok === false ? "error" : "done"
          break
        }
      }
      continue
    }

    const parts: Part[] = []
    if (item.content) parts.push({ kind: "text", text: item.content })
    const calls = item.metadata?.tool_calls
    if (item.role === "assistant" && Array.isArray(calls)) {
      for (const call of calls as Array<Record<string, unknown>>) {
        const fn = (call.function ?? {}) as Record<string, unknown>
        parts.push({
          kind: "tool",
          id: String(call.id ?? nextId("t")),
          name: String(fn.name ?? "tool"),
          args: parseArgs(fn.arguments),
          status: "done",
          startedAt: 0,
        })
      }
    }

    out.push({
      id: nextId("m"),
      role: item.role === "assistant" ? "assistant" : "user",
      content: item.content,
      parts,
      createdAt: item.created_at,
    })
  }

  // A tool step and the final answer are stored as separate assistant rows.
  // Show them as one card: one "gemma" header, one stats line at the end.
  const merged: Message[] = []
  for (const message of out) {
    const previous = merged[merged.length - 1]
    if (
      previous &&
      previous.role === "assistant" &&
      message.role === "assistant" &&
      previous.parts.some((part) => part.kind === "tool")
    ) {
      merged[merged.length - 1] = {
        ...previous,
        content: message.content || previous.content,
        parts: [
          ...previous.parts,
          ...message.parts.filter((part) => part.kind !== "tool" || !previous.parts.some(
            (existing) => existing.kind === "tool" && existing.id === part.id,
          )),
        ],
      }
      continue
    }
    merged.push(message)
  }
  return merged
}

export function createChat(client: GemmaClient) {
  const [sessions, setSessions] = createSignal<Session[]>([])
  const [activeId, setActiveId] = createSignal("")
  const [streaming, setStreaming] = createSignal(false)
  const [streamingId, setStreamingId] = createSignal<string | null>(null)
  const [showThinking, setShowThinking] = createSignal(false)
  const [info, setInfo] = createSignal<ServerInfo | null>(null)
  const [models, setModels] = createSignal<ModelList | null>(null)
  const [providers, setProviders] = createSignal<ProviderCatalog | null>(null)
  const [commands, setCommands] = createSignal<CommandDef[]>([])
  const [notice, setNoticeSignal] = createSignal<string | null>(null)
  // Web search defaults ON, like the classic TUI (websearch_enabled = True).
  const [websearch, setWebsearchSignal] = createSignal(true)
  const [connected, setConnected] = createSignal<boolean | null>(null)
  const [permission, setPermission] = createSignal<AgentEvent | null>(null)
  const [runId, setRunId] = createSignal<string | null>(null)
  const [live, setLive] = createSignal<{ tokens: number; elapsed: number; ttft: number; genMs?: number }>({
    tokens: 0,
    elapsed: 0,
    ttft: 0,
  })

  let controller: AbortController | null = null
  let streamingFlag = false
  let noticeTimer: ReturnType<typeof setTimeout> | undefined

  /** Post a transient message; it clears itself so repeats are visible again. */
  function setNotice(text: string | null): void {
    setNoticeSignal(text)
    if (noticeTimer) clearTimeout(noticeTimer)
    if (text) noticeTimer = setTimeout(() => setNoticeSignal(null), 6000)
  }

  function setWebsearch(next: boolean): void {
    setWebsearchSignal(next)
    setNotice(`Web search: ${next ? "ON" : "OFF"}`)
  }

  function toggleWebsearch(): void {
    setWebsearch(!websearch())
  }

  const activeSession = createMemo<Session | undefined>(
    () => sessions().find((session) => session.id === activeId()) ?? sessions()[0],
  )

  const mode = createMemo<"chat" | "agent">(() => info()?.mode ?? "chat")

  const patchSession = (id: string, patch: (session: Session) => Session) => {
    setSessions((prev) => prev.map((session) => (session.id === id ? patch(session) : session)))
  }

  const patchMessage = (sessionId: string, messageId: string, patch: (message: Message) => Message) => {
    patchSession(sessionId, (session) => ({
      ...session,
      updatedAt: new Date().toISOString(),
      messages: session.messages.map((message) => (message.id === messageId ? patch(message) : message)),
    }))
  }

  /** Load the server's sessions, newest first. */
  /** Ping /health so the header dot reflects the connection. Runs every 30s. */
  async function pollHealth(): Promise<void> {
    try {
      await client.health()
      setConnected(true)
    } catch {
      setConnected(false)
    }
  }
  void pollHealth()
  setInterval(() => void pollHealth(), 30_000)

  async function load(): Promise<void> {
    const serverInfo = await client.info()
    setInfo(serverInfo)

    const metas = await client.sessions()
    const details = await Promise.all(
      metas.map(async (meta) => {
        try {
          return await client.session(meta.id)
        } catch {
          return null
        }
      }),
    )
    const loaded: Session[] = details
      .filter((detail): detail is NonNullable<typeof detail> => detail !== null)
      .map((detail) => ({
        id: detail.id,
        title: detail.title || "New chat",
        updatedAt: detail.updated_at,
        messages: fromServer(detail.messages),
      }))

    // Every launch starts on a fresh, empty chat. Reuse an existing empty
    // server session when there is one, otherwise keep a purely local one
    // until the first message is actually sent (no DB row before then).
    const empty = loaded.find((session) => session.messages.length === 0)
    const starter: Session = empty ?? {
      id: nextId("local-"),
      title: "New chat",
      updatedAt: new Date().toISOString(),
      messages: [],
    }
    setSessions([starter, ...loaded.filter((session) => session.id !== starter.id)])
    setActiveId(starter.id)
  }

  async function loadModels(): Promise<ModelList | null> {
    try {
      const payload = await client.models()
      setModels(payload)
      return payload
    } catch {
      return null
    }
  }

  async function loadProviders(): Promise<ProviderCatalog | null> {
    try {
      const payload = await client.providers()
      setProviders(payload)
      return payload
    } catch {
      return null
    }
  }

  async function loadCommands(): Promise<void> {
    try {
      setCommands(await client.commands())
    } catch {
      // The client ships its own command list as a fallback.
    }
  }

  async function newChat(): Promise<void> {
    // Reuse an existing empty chat instead of piling up "New chat" DB rows.
    const empty = sessions().find((session) => session.messages.length === 0)
    if (empty) {
      setSessions((prev) => [
        { ...empty, title: "New chat", updatedAt: new Date().toISOString() },
        ...prev.filter((session) => session.id !== empty.id),
      ])
      setActiveId(empty.id)
      setNotice(null)
      return
    }
    const local: Session = {
      id: nextId(LOCAL_SESSION_PREFIX),
      title: "New chat",
      updatedAt: new Date().toISOString(),
      messages: [],
    }
    setSessions((prev) => [local, ...prev])
    setActiveId(local.id)
    setNotice(null)
  }

  function select(id: string): void {
    setActiveId(id)
  }

  async function remove(id: string): Promise<void> {
    const remaining = sessions().filter((session) => session.id !== id)
    if (activeId() === id) {
      controller?.abort()
      controller = null
      streamingFlag = false
      setStreaming(false)
      setStreamingId(null)
    }
    if (remaining.length === 0) {
      setSessions([])
      setActiveId("")
      await newChat()
    } else {
      setSessions(remaining)
      const nextId = remaining[0]?.id
      if (activeId() === id && nextId) setActiveId(nextId)
    }
    try {
      await client.deleteSession(id)
    } catch {
      // The local list already dropped it.
    }
  }

  async function clearChat(): Promise<void> {
    const session = activeSession()
    if (!session) return
    patchSession(session.id, (current) => ({
      ...current,
      title: "New chat",
      updatedAt: new Date().toISOString(),
      messages: [],
    }))
    try {
      await client.clearSession(session.id)
    } catch {
      // Keep the local clear.
    }
  }

  async function renameChat(title: string): Promise<void> {
    const session = activeSession()
    const clean = title.trim()
    if (!session || !clean) return
    patchSession(session.id, (current) => ({
      ...current,
      title: clean.slice(0, 60),
      updatedAt: new Date().toISOString(),
    }))
    try {
      await client.renameSession(session.id, clean)
    } catch {
      // The optimistic title stays.
    }
  }

  async function setModel(name: string, provider?: string): Promise<void> {
    try {
      await client.switchModel(name, provider)
      setInfo((prev) => (prev ? { ...prev, model: name } : prev))
      setModels((prev) => (prev ? { ...prev, active: name, models: prev.models.map((m) => ({ ...m, active: m.name === name })) } : prev))
      setProviders((prev) => {
        if (!prev) return prev
        const chosen = provider ?? prev.active.provider
        return {
          ...prev,
          active: { provider: chosen, model: name },
          providers: prev.providers.map((item) => ({
            ...item,
            models: item.models.map((m) => ({
              ...m,
              active: item.id === chosen && m.name === name,
            })),
          })),
        }
      })
      setNotice(`Model set to ${displayModelName(name)}`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  async function setMode(next: "chat" | "agent"): Promise<void> {
    if (streamingFlag) {
      setNotice("Wait for the current reply to finish")
      return
    }
    try {
      await client.setMode(next)
      setInfo((prev) => (prev ? { ...prev, mode: next } : prev))
      setNotice(next === "agent" ? "Agent mode: tools enabled" : "Chat mode: plain replies")
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  function toggleMode(): void {
    void setMode(mode() === "agent" ? "chat" : "agent")
  }

  async function setProviderMode(next: "auto" | "native" | "prompt"): Promise<void> {
    try {
      await client.setProviderMode(next)
      setInfo((prev) => (prev ? { ...prev, provider_mode: next } : prev))
      setNotice(`Provider mode: ${next}`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  async function setContextLimit(limit: number): Promise<void> {
    try {
      const result = await client.setContextLimit(limit)
      setInfo((prev) => (prev ? { ...prev, context_max: result.context_max } : prev))
      setNotice(`Context limit: ${result.context_max.toLocaleString()} tokens`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  async function updatePermissions(
    patch: Parameters<GemmaClient["setPermissionConfig"]>[0],
  ): Promise<PermissionConfig | null> {
    try {
      const config = await client.setPermissionConfig(patch)
      setInfo((prev) => (prev ? { ...prev, permission_mode: config.mode } : prev))
      return config
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
      return null
    }
  }

  async function permissionConfig(): Promise<PermissionConfig | null> {
    try {
      return await client.permissionConfig()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
      return null
    }
  }

  async function setAgentSettings(patch: {
    workspace?: string
    max_steps?: number
  }): Promise<void> {
    try {
      const result = (await client.setAgentSettings(patch)) as {
        workspace: string
        max_steps: number
      }
      setInfo((prev) =>
        prev ? { ...prev, workspace: result.workspace, max_steps: result.max_steps } : prev,
      )
      if (patch.max_steps !== undefined) setNotice(`Agent max steps: ${result.max_steps}`)
      if (patch.workspace !== undefined) setNotice(`Agent workspace: ${result.workspace}`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  /** Status snapshot or action result for MCP / skills / LSP. */
  async function serviceStatus(service: "mcp" | "skills" | "lsp", action: string): Promise<string | null> {
    try {
      if (action === "status" || (service === "skills" && action === "list")) {
        if (service === "mcp") return (await client.mcpStatus()).status
        if (service === "lsp") return (await client.lspStatus()).status
        return (await client.skillsSummary()).summary
      }
      const result = await client.serviceAction(service, action)
      return result.status
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
      return null
    }
  }

  /** Fold the active session into a checkpoint and reload it. */
  async function compactChat(instructions = ""): Promise<void> {
    const session = activeSession()
    if (!session) return
    setNotice("✦ Compacting…")
    try {
      const result = await client.compactSession(session.id, instructions)
      if (!result.ok) {
        setNotice(result.reason === "nothing to compact yet" ? "Nothing to compact yet" : "Compaction skipped")
        return
      }
      setNotice(
        `Compacted: ${result.removed} messages removed (${result.before_tokens} → ${result.after_tokens} tokens)`,
      )
      await load()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  async function refreshTitles(): Promise<void> {
    try {
      const metas = await client.sessions()
      setSessions((prev) =>
        prev.map((session) => {
          const meta = metas.find((item) => item.id === session.id)
          return meta ? { ...session, title: meta.title, updatedAt: meta.updated_at } : session
        }),
      )
    } catch {
      // Titles are cosmetic.
    }
  }

  /** Replace the optimistic transcript with the server's persisted one. */
  async function reconcile(sessionId: string): Promise<void> {
    try {
      const detail = await client.session(sessionId)
      const canonical = fromServer(detail.messages)
      const local = sessions().find((session) => session.id === sessionId)
      // Only trust the server when it actually stored this turn.
      if (!local || canonical.length < local.messages.length) return
      // Server messages have no timing stats; carry the just-measured ones
      // over by role+content so the ⚡ line survives the swap.
      const stats = new Map(
        local.messages
          .filter((message) => typeof message.elapsed === "number")
          .map((message) => [`${message.role}:${message.content}`, message]),
      )
      for (const message of canonical) {
        const prior = stats.get(`${message.role}:${message.content}`)
        if (prior) {
          message.elapsed = prior.elapsed
          message.ttft = prior.ttft
          message.tokens = prior.tokens
          message.genMs = prior.genMs
          message.toolMs = prior.toolMs
        }
      }
      patchSession(sessionId, (current) => ({
        ...current,
        title: detail.title || current.title,
        updatedAt: detail.updated_at,
        messages: canonical,
      }))
    } catch {
      // The optimistic transcript stays.
    }
  }

  // Titles arrive from a background model call after the first reply.
  setInterval(() => {
    if (!streamingFlag) void refreshTitles()
  }, 4000)

  async function startTurn(raw: string): Promise<{
    session: Session
    sessionId: string
    assistantId: string
    startedAt: number
  } | null> {
    const text = raw.trim()
    const current = activeSession()
    if (!text || streamingFlag || !current) return null

    // A local placeholder becomes a real session only when the first message
    // is sent, so empty chats never pile up in the database.
    let session = current
    let sessionId = session.id
    if (isLocalSession(sessionId)) {
      try {
        const realId = await client.createSession()
        session = { ...session, id: realId }
        sessionId = realId
        setSessions((prev) => prev.map((item) => (item.id === current.id ? session : item)))
        setActiveId(realId)
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error))
        return null
      }
    }

    const now = new Date().toISOString()
    const userMessage: Message = {
      id: nextId("u"),
      role: "user",
      content: text,
      parts: [{ kind: "text", text }],
      createdAt: now,
    }
    const assistantId = nextId("a")
    const assistantMessage: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      parts: [],
      createdAt: now,
    }

    patchSession(sessionId, (current) => ({
      ...current,
      // "New chat" stays until the server's background title arrives.
      updatedAt: now,
      messages: [...current.messages, userMessage, assistantMessage],
    }))

    controller = new AbortController()
    streamingFlag = true
    setStreaming(true)
    setStreamingId(assistantId)
    setLive({ tokens: 0, elapsed: 0, ttft: 0 })
    return { session, sessionId, assistantId, startedAt: performance.now() }
  }

  function finishTurn(): void {
    controller = null
    streamingFlag = false
    setStreaming(false)
    setStreamingId(null)
    setPermission(null)
    setRunId(null)
  }

  function applyFailure(
    sessionId: string,
    assistantId: string,
    error: unknown,
    content: string,
    stopped: boolean,
  ): void {
    const aborted = stopped || (error instanceof DOMException && error.name === "AbortError")
    if (aborted) {
      patchMessage(sessionId, assistantId, (message) => ({
        ...message,
        content: content || "_Generation stopped._",
        stopped: true,
      }))
      return
    }
    if (error instanceof PairingError) {
      patchMessage(sessionId, assistantId, (message) => ({
        ...message,
        content: content || "**Pairing required.** Restart `gemma` to mint a fresh pairing link.",
        error: "pairing required",
      }))
      return
    }
    const text = error instanceof Error ? error.message : String(error)
    patchMessage(sessionId, assistantId, (current) => ({
      ...current,
      content: current.content,
      error: text,
    }))
  }

  function domainOf(url: string): string {
    try {
      return new URL(url).hostname.replace(/^www\./, "")
    } catch {
      return url
    }
  }

  /**
   * Judge + fetch web context for a turn. Reflects progress on the assistant
   * message itself: "✦ Searching the web…" while running, then either the
   * sources used or a clear failure line (never a silent memory answer).
   */
  async function prepareSearch(
    sessionId: string,
    assistantId: string,
    raw: string,
  ): Promise<{
    context?: string
    sources?: Array<{ title: string; domain: string; date?: string }>
    memoryFallback?: boolean
  }> {
    const mark = (search: SearchState | undefined) =>
      patchMessage(sessionId, assistantId, (message) => ({ ...message, search }))

    // Greetings/chit-chat never search (same gate as the classic TUI).
    if (SMALLTALK_RE.test(raw.trim())) {
      mark(undefined)
      return {}
    }

    // Phase 1: judge + rewrite + SearXNG search (fast, no page fetches).
    const history = (activeSession()?.messages ?? [])
      .slice(-7, -1)
      .filter((message) => message.content.trim())
      .map((message) => `${message.role === "user" ? "User" : "Assistant"}: ${message.content.slice(0, 400)}`)
    mark({ status: "searching" })
    try {
      const prep = await client.researchPrepare(raw.trim(), history, false)
      if (!prep.search) {
        mark(undefined)
        return {}
      }
      const label = prep.rewritten ?? prep.query ?? raw.trim()
      if (!prep.results?.length) {
        mark({
          status: "failed",
          query: label,
          error: prep.entity ? `Couldn't find reliable results for ${prep.entity}` : (prep.detail ?? "no results"),
        })
        return { memoryFallback: true }
      }

      const sources = prep.results.map((result) => ({
        title: result.title ?? "",
        domain: domainOf(result.url ?? ""),
        date: result.date ?? "",
      }))

      // Phase 2: read the top pages (server fetches them in parallel).
      mark({ status: "reading", query: label, count: sources.length, sources })
      const read = await client.researchRead(prep.query ?? raw.trim(), prep.results)
      const used = (read.results ?? []).filter((result) => result.excerpt)
      const dated = used.map((result) => ({
        title: result.title ?? "",
        domain: domainOf(result.url ?? ""),
        date: result.date ?? "",
      }))
      if (read.ok && read.context && dated.length) {
        mark({ status: "ok", query: label, count: dated.length, sources: dated })
        return { context: read.context, sources: dated }
      }
      mark({
        status: "failed",
        query: label,
        error: read.error ?? "no page text could be fetched",
      })
      return { memoryFallback: true }
    } catch (error) {
      mark({ status: "failed", error: error instanceof Error ? error.message : String(error) })
      return { memoryFallback: true }
    }
  }

  async function sendChat(raw: string): Promise<void> {
    const turn = await startTurn(raw)
    if (!turn) return
    const { sessionId, assistantId, startedAt } = turn
    let firstAt: number | null = null
    let tokens = 0
    let content = ""
    let memoryFallback = false
    const statsBox: { value: DoneStats | null } = { value: null }

    // Web research: the server judges the question (with the same heuristics
    // as the classic TUI) and fetches SearXNG context, which rides into the
    // request as a system message. Sources show under the answer.
    let context: string | undefined
    if (websearch()) {
      const prep = await prepareSearch(sessionId, assistantId, raw.trim())
      context = prep.context
      memoryFallback = prep.memoryFallback === true
    }

    try {
      await client.streamChat(
        sessionId,
        raw.trim(),
        {
          onStart: () => {
            if (context) setNotice(null)
          },
          onToken: (token) => {
            if (firstAt === null) firstAt = performance.now()
            content += token
            tokens += 1
            const elapsed = (performance.now() - startedAt) / 1000
            setLive({ tokens, elapsed, ttft: firstAt ? (firstAt - startedAt) / 1000 : 0 })
            patchMessage(sessionId, assistantId, (message) => ({
              ...message,
              content,
              parts: [{ kind: "text", text: content }],
            }))
          },
          onDone: (done) => {
            statsBox.value = done
          },
        },
        controller?.signal,
        context,
      )

      const finalStats = statsBox.value
      const elapsedVal = finalStats?.elapsed ?? (performance.now() - startedAt) / 1000
      const ttftVal = finalStats?.ttft ?? (firstAt ? (firstAt - startedAt) / 1000 : elapsedVal)
      patchMessage(sessionId, assistantId, (message) => {
        const patched = {
          ...message,
          content,
          elapsed: elapsedVal,
          ttft: ttftVal,
          tokens: finalStats?.tokens,
        }
        return memoryFallback ? markMemoryFallback(patched) : patched
      })
      void refreshTitles()
    } catch (error) {
      applyFailure(sessionId, assistantId, error, content, false)
    } finally {
      finishTurn()
    }
  }

  async function sendAgent(raw: string): Promise<void> {
    const turn = await startTurn(raw)
    if (!turn) return
    const { sessionId, assistantId, startedAt } = turn
    let firstAt: number | null = null
    let tokens = 0
    let genMs = 0
    let stopped = false
    let failure: string | null = null
    let currentRun = ""
    let memoryFallback = false

    // Web research rides into the agent run as extra system context, so tool
    // work still happens but facts come from the fresh results.
    let context: string | undefined
    if (websearch()) {
      const prep = await prepareSearch(sessionId, assistantId, raw.trim())
      context = prep.context
      memoryFallback = prep.memoryFallback === true
    }

    const update = (patch: (message: Message) => Message) =>
      patchMessage(sessionId, assistantId, patch)

    try {
      await client.streamAgent(
        sessionId,
        raw.trim(),
        {
          onEvent: (event: AgentEvent) => {
            switch (event.type) {
              case "run":
                currentRun = event.run_id ?? ""
                setRunId(currentRun)
                break
              case "text_replace": {
                // The model emitted tool-call markup as text; the server
                // recovered the call and now sends the prose-only version.
                const replacement = typeof event.content === "string" ? event.content : ""
                update((message) => {
                  const parts = [...message.parts]
                  while (parts.length && parts[parts.length - 1]?.kind === "text") parts.pop()
                  if (replacement) parts.push({ kind: "text", text: replacement })
                  return { ...message, parts, content: textOf(parts) }
                })
                break
              }
              case "text": {
                const chunk = typeof event.content === "string" ? event.content : ""
                if (!chunk) break
                if (firstAt === null) firstAt = performance.now()
                tokens += 1
                setLive({
                  tokens,
                  elapsed: (performance.now() - startedAt) / 1000,
                  ttft: firstAt ? (firstAt - startedAt) / 1000 : 0,
                })
                update((message) => {
                  const parts = appendChunk(message.parts, "text", chunk)
                  return { ...message, parts, content: textOf(parts) }
                })
                break
              }
              case "reasoning": {
                const chunk = typeof event.content === "string" ? event.content : ""
                if (!chunk) break
                update((message) => ({
                  ...message,
                  parts: appendChunk(message.parts, "reasoning", chunk),
                }))
                break
              }
              case "tool_start": {
                const info = (event.content ?? {}) as Record<string, unknown>
                update((message) => {
                  // Clear the stream buffer when a tool starts: a stray short
                  // text fragment (the "3" in the screenshot) must never sit
                  // on the tool line. Reasoning stays as its own collapsed
                  // block, which the user can expand after the turn.
                  const parts = [...message.parts]
                  const last = parts[parts.length - 1]
                  if (
                    last?.kind === "text" &&
                    last.text.trim().length <= 1 &&
                    !message.parts.some((part) => part.kind === "tool")
                  ) {
                    parts.pop()
                  }
                  return {
                    ...message,
                    parts: [
                      ...parts,
                      {
                        kind: "tool",
                        id: String(info.id ?? nextId("t")),
                        name: String(info.name ?? "tool"),
                        args: parseArgs(info.args),
                        status: "running",
                        startedAt: Date.now(),
                      },
                    ],
                  }
                })
                break
              }
              case "tool_finish": {
                const info = (event.content ?? {}) as Record<string, unknown>
                const id = String(info.id ?? "")
                update((message) => ({
                  ...message,
                  parts: message.parts.map((part) =>
                    part.kind === "tool" && part.id === id
                      ? {
                          ...part,
                          status: info.ok === false ? "error" : "done",
                          result: String(info.result ?? ""),
                          finishedAt: Date.now(),
                        }
                      : part,
                  ),
                }))
                break
              }
              case "usage": {
                const info = (event.content ?? {}) as Record<string, unknown>
                const output = Number(info.output_tokens ?? 0)
                if (output > 0) tokens = Math.max(tokens, output)
                const eventGenMs = Number(info.gen_ms ?? 0)
                if (eventGenMs > 0) genMs = eventGenMs
                setLive({
                  tokens,
                  elapsed: (performance.now() - startedAt) / 1000,
                  ttft: firstAt ? (firstAt - startedAt) / 1000 : 0,
                  genMs: eventGenMs > 0 ? eventGenMs : undefined,
                })
                break
              }
              case "permission_request":
                setPermission(event)
                break
              case "complete": {
                const final = typeof event.content === "string" ? event.content : ""
                if (final) {
                  update((message) => {
                    const parts = message.parts.some((part) => part.kind === "text")
                      ? message.parts
                      : appendChunk(message.parts, "text", final)
                    return { ...message, parts, content: textOf(parts) }
                  })
                }
                break
              }
              case "error":
                failure = event.message || String(event.content ?? "The agent failed.")
                break
              case "aborted":
                stopped = true
                break
              default:
                break
            }
          },
        },
        controller?.signal,
        context,
      )

      const elapsed = (performance.now() - startedAt) / 1000
      update((message) => {
        const toolMs = message.parts.reduce(
          (sum, part) =>
            part.kind === "tool" && part.finishedAt && part.startedAt
              ? sum + (part.finishedAt - part.startedAt)
              : sum,
          0,
        )
        const patched = {
          ...message,
          elapsed,
          ttft: firstAt ? (firstAt - startedAt) / 1000 : elapsed,
          tokens: tokens || undefined,
          genMs: genMs || undefined,
          toolMs: toolMs || undefined,
          stopped: stopped || message.stopped,
          error: failure ?? message.error,
        }
        return memoryFallback ? markMemoryFallback(patched) : patched
      })
      finishTurn()
      await reconcile(sessionId)
      void refreshTitles()
    } catch (error) {
      const aborted = stopped || (error instanceof DOMException && error.name === "AbortError")
      applyFailure(sessionId, assistantId, error, "", aborted)
      finishTurn()
      await reconcile(sessionId)
    } finally {
      finishTurn()
    }
  }

  function send(raw: string): Promise<void> {
    return mode() === "agent" ? sendAgent(raw) : sendChat(raw)
  }

  function stop(): void {
    const current = runId()
    if (current) {
      void client.abortRun(current).catch(() => {
        // The run may already have finished.
      })
    }
    controller?.abort()
  }

  async function respondPermission(decision: "allow" | "always" | "deny"): Promise<void> {
    const request = permission()
    setPermission(null)
    if (!request?.id) return
    try {
      await client.respondPermission(request.id, decision)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const metrics = createMemo<Metrics>(() => {
    const session = activeSession()
    const serverInfo = info()
    const contextUsed = (session?.messages ?? []).reduce(
      (sum, message) => sum + estimateTokens(message.content),
      0,
    )
    const contextMax = serverInfo?.context_max ?? 32768
    const provider = serverInfo?.provider ?? "llama.cpp"
    const temperature = serverInfo?.temperature ?? 0.7

    if (streaming()) {
      const { tokens, elapsed, ttft, genMs } = live()
      const genSeconds = (genMs ?? 0) / 1000
      return {
        contextUsed,
        contextMax,
        tokens,
        elapsed,
        tokPerSec: genSeconds > 0
          ? tokens / genSeconds
          : ttft > 0
            ? tokens / Math.max(elapsed - ttft, 0.001)
            : 0,
        provider,
        temperature,
        websearch: websearch(),
      }
    }

    const lastAssistant = [...(session?.messages ?? [])]
      .reverse()
      .find((message) => message.role === "assistant" && typeof message.elapsed === "number")
    if (!lastAssistant) {
      return {
        contextUsed,
        contextMax,
        tokens: 0,
        elapsed: 0,
        tokPerSec: 0,
        provider,
        temperature,
        websearch: websearch(),
      }
    }
    const elapsed = lastAssistant.elapsed ?? 0
    const genSeconds = (lastAssistant.genMs ?? 0) / 1000
    const tokens = lastAssistant.tokens ?? estimateTokens(lastAssistant.content)
    return {
      contextUsed,
      contextMax,
      tokens,
      elapsed,
      tokPerSec: genSeconds > 0 ? tokens / genSeconds : 0,
      provider,
      temperature,
      websearch: websearch(),
    }
  })

  return {
    client,
    sessions,
    activeSession,
    activeId,
    info,
    models,
    providers,
    commands,
    notice,
    metrics,
    streaming,
    streamingId,
    permission,
    runId,
    mode,
    load,
    loadModels,
    loadProviders,
    loadCommands,
    newChat,
    select,
    remove,
    clearChat,
    renameChat,
    setModel,
    setMode,
    toggleMode,
    setProviderMode,
    setContextLimit,
    updatePermissions,
    permissionConfig,
    setAgentSettings,
    serviceStatus,
    websearch,
    setWebsearch,
    toggleWebsearch,
    showThinking,
    toggleThinking: () => setShowThinking((value) => !value),
    connected,
    compactChat,
    send,
    stop,
    respondPermission,
    setNotice,
  }
}

export type Chat = ReturnType<typeof createChat>
