/**
 * Terminal-level tests for the OpenTUI client.
 *
 * A small Bun server stands in for the Python backend, so these exercise the
 * real renderer, the real key handling and the real SSE parsing.
 */

import { afterEach, describe, expect, test } from "bun:test"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { testRender } from "@opentui/solid"
import type { TestRendererSetup } from "@opentui/core/testing"
import { GemmaClient } from "./api"
import { createChat } from "./chat"
import { App } from "./app"
import { activeThemeId, activeThemeMode, setTheme, setThemeMode } from "./theme"

// Theme changes persist to prefs; keep them out of the real user config.
process.env.XDG_CONFIG_HOME = mkdtempSync(join(tmpdir(), "gemma-tui-test-"))

const NOW = new Date().toISOString()

const MODELS = {
  active: "test-model",
  models: [
    { name: "test-model", source: "current", active: true },
    { name: "other-model", source: "local", active: false },
  ],
}

const PROVIDERS = {
  default: "llama.cpp",
  active: { provider: "llama.cpp", model: "test-model" },
  providers: [
    {
      id: "llama.cpp",
      name: "llama.cpp (local)",
      kind: "local",
      base_url: "http://127.0.0.1:8080",
      is_default: true,
      default_model: "test-model",
      models: MODELS.models,
    },
    {
      id: "ollama",
      name: "Ollama",
      kind: "openai",
      base_url: "http://127.0.0.1:11434",
      is_default: false,
      default_model: "qwen2.5-coder",
      models: [{ name: "qwen2.5-coder", source: "remote", active: false }],
    },
  ],
}

const COMMANDS = {
  commands: [
    { name: "/new", description: "Start a new chat" },
    { name: "/clear", description: "Clear this chat" },
    { name: "/help", description: "Show commands and keys" },
    // Server-side commands the client does not implement must still surface.
    { name: "/compact", description: "Compact the conversation" },
  ],
}

function sseResponse(events: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) controller.enqueue(encoder.encode(`data: ${event}\n\n`))
      controller.close()
    },
  })
  return new Response(body, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
  })
}

interface MockServer {
  base: string
  chatRequests: Array<{ session_id: string; message: string; context?: string }>
  agentRequests: Array<{ session_id: string; message: string; context?: string }>
  prepareCalls: string[]
  modeRequests: string[]
  providerModes: string[]
  contextLimits: number[]
  agentSettings: Array<{ workspace?: string; max_steps?: number }>
  permissionConfig: { mode: string; rules: string[] }
  stop: () => void
}

function startMock(
  replyTokens = ["Hello", " from", " the", " mock"],
  options: { mode?: "chat" | "agent" } = {},
): MockServer {
  const chatRequests: Array<{ session_id: string; message: string; context?: string }> = []
  const agentRequests: Array<{ session_id: string; message: string; context?: string }> = []
  const prepareCalls: string[] = []
  const modeRequests: string[] = []
  const encoder = new TextEncoder()
  let permissionStream: ReadableStreamDefaultController<Uint8Array> | null = null

  const info = {
    app: "gemma",
    version: "0.1.0",
    model: "test-model",
    mode: options.mode ?? "chat",
    workspace: "/tmp",
    provider: "llama.cpp",
    temperature: 0.7,
    context_max: 8192,
    max_steps: 10,
    permission_mode: "auto",
    provider_mode: "auto",
  }
  const permissionConfig = { mode: "auto", rules: [] as string[] }
  const providerModes: string[] = []
  const contextLimits: number[] = []
  const agentSettings: Array<{ workspace?: string; max_steps?: number }> = []

  const server = Bun.serve({
    port: 0,
    async fetch(request) {
      const url = new URL(request.url)
      const path = url.pathname

      if (path === "/api/info") return Response.json(info)
      if (path === "/health") return Response.json({ status: "ok", app: "gemma" })
      if (path === "/api/sessions" && request.method === "GET") {
        return Response.json({ sessions: [{ id: "s1", title: "First chat", updated_at: NOW, message_count: 0 }] })
      }
      if (path === "/api/sessions" && request.method === "POST") {
        return Response.json({ id: "s2" }, { status: 201 })
      }
      if (path === "/api/sessions/s1") {
        return Response.json({ id: "s1", title: "First chat", updated_at: NOW, messages: [] })
      }
      if (path === "/api/models" && request.method === "GET") return Response.json(MODELS)
      if (path === "/api/models" && request.method === "POST") return Response.json({ ok: true })
      if (path === "/api/providers") return Response.json(PROVIDERS)
      if (path === "/api/mode" && request.method === "POST") {
        const body = (await request.json()) as { mode: string }
        modeRequests.push(body.mode)
        info.mode = body.mode as "chat" | "agent"
        return Response.json({ ok: true, mode: body.mode })
      }
      if (path === "/api/commands") return Response.json(COMMANDS)
      if (path === "/api/pair") return Response.json({ pair: "http://127.0.0.1/pair?token=abc" })
      if (path === "/api/permissions/config" && request.method === "GET") {
        return Response.json(permissionConfig)
      }
      if (path === "/api/permissions/config" && request.method === "POST") {
        const body = (await request.json()) as {
          mode?: string
          add?: string
          effect?: "allow" | "deny" | "ask"
          action?: string
          resource?: string
          reset?: boolean
        }
        if (body.reset) {
          permissionConfig.rules = []
        } else if (body.mode) {
          if (!["auto", "ask", "readonly"].includes(body.mode)) {
            return Response.json({ error: "invalid mode" }, { status: 400 })
          }
          permissionConfig.mode = body.mode
          info.permission_mode = body.mode
        } else if (body.add) {
          if (!body.effect) return Response.json({ error: "invalid effect" }, { status: 400 })
          permissionConfig.rules.push(
            `${body.action ?? "*"}:${body.resource ?? "*"}:${body.effect}`,
          )
        }
        return Response.json({ mode: permissionConfig.mode, rules: [...permissionConfig.rules] })
      }
      if (path === "/api/provider_mode" && request.method === "POST") {
        const body = (await request.json()) as { mode: string }
        if (!["auto", "native", "prompt"].includes(body.mode)) {
          return Response.json({ error: "invalid mode" }, { status: 400 })
        }
        providerModes.push(body.mode)
        info.provider_mode = body.mode
        return Response.json({ ok: true, provider_mode: body.mode })
      }
      if (path === "/api/context" && request.method === "POST") {
        const body = (await request.json()) as { limit: number }
        if (typeof body.limit !== "number" || Number.isNaN(body.limit)) {
          return Response.json({ error: "limit must be a number" }, { status: 400 })
        }
        const clamped = Math.max(2000, Math.min(body.limit, 1_000_000))
        contextLimits.push(clamped)
        info.context_max = clamped
        return Response.json({ ok: true, context_max: clamped })
      }
      if (path === "/api/agent/settings" && request.method === "POST") {
        const body = (await request.json()) as { workspace?: string; max_steps?: number }
        agentSettings.push(body)
        if (body.workspace !== undefined) info.workspace = body.workspace
        if (body.max_steps !== undefined) info.max_steps = body.max_steps
        return Response.json({ ok: true, workspace: info.workspace, max_steps: info.max_steps })
      }
      if (path === "/api/mcp/status") {
        return Response.json({ enabled: true, status: "MCP servers:\n- files: connected (2 tools)" })
      }
      if (path === "/api/skills" && request.method === "GET") {
        return Response.json({ enabled: true, summary: "Available skills:\n- commit: write commits" })
      }
      if (path === "/api/lsp/status") {
        return Response.json({ enabled: true, status: "LSP servers:\n- python: pyright (running)" })
      }
      if (
        ["/api/mcp", "/api/skills", "/api/lsp"].includes(path) &&
        request.method === "POST"
      ) {
        const body = (await request.json()) as { action: string }
        const service = path.slice(5)
        const allowed: Record<string, string[]> = {
          mcp: ["start", "reload", "stop", "status"],
          skills: ["list", "reload"],
          lsp: ["status", "stop"],
        }
        if (!allowed[service]?.includes(body.action)) {
          return Response.json({ error: "bad action" }, { status: 400 })
        }
        return Response.json({
          ok: true,
          service,
          action: body.action,
          status: `${service} ${body.action} ok`,
        })
      }
      if (path === "/api/research/status") {
        return Response.json({
          healthy: false,
          detail: "connection refused",
          url: "http://localhost:8888",
          deep_read: true,
        })
      }
      if (path === "/api/research/prepare" && request.method === "POST") {
        const body = (await request.json()) as { message: string }
        prepareCalls.push(body.message)
        if (/latest|breaking/i.test(body.message)) {
          return Response.json({
            search: true,
            query: "gemma latest news",
            context: "WEB CONTEXT",
            results: [
              { title: "Gemma release notes", url: "https://ai.google.dev/gemma", engine: "searxng" },
              { title: "Gemma on Wikipedia", url: "https://en.wikipedia.org/wiki/Gemma", engine: "wikipedia" },
            ],
          })
        }
        if (/chief minister|cm of/i.test(body.message)) {
          return Response.json({
            search: true,
            query: "List of chief ministers of Tamil Nadu",
            context: "OFFICE CONTEXT",
            results: [
              { title: "List of chief ministers of Tamil Nadu", url: "https://en.wikipedia.org/wiki/List_of_chief_ministers_of_Tamil_Nadu", engine: "wikipedia" },
            ],
          })
        }
        return Response.json({ search: false })
      }
      if (path === "/api/research/test" && request.method === "POST") {
        const body = (await request.json()) as { query: string }
        return Response.json({
          ok: true,
          query: body.query,
          results: [
            { title: "Gemma release notes", url: "https://example.com/gemma", engine: "duckduckgo" },
          ],
        })
      }
      if (path === "/api/research/config" && request.method === "POST") {
        const body = (await request.json()) as { url: string }
        return Response.json({ ok: true, url: body.url, healthy: false, detail: "connection refused" })
      }
      if (path === "/api/init" && request.method === "POST") {
        return Response.json({ ok: true, path: "/workspace/AGENTS.md" })
      }
      if (path === "/api/compact" && request.method === "POST") {
        const body = (await request.json()) as { session_id: string }
        if (body.session_id === "s1") {
          return Response.json({
            ok: true,
            removed: 6,
            before_tokens: 12000,
            after_tokens: 2400,
            summary: "## Checkpoint",
          })
        }
        return Response.json({ ok: false, reason: "nothing to compact yet" })
      }

      if (path === "/api/chat" && request.method === "POST") {
        const body = (await request.json()) as { session_id: string; message: string }
        chatRequests.push(body)
        return sseResponse([
          JSON.stringify({ type: "start", session_id: body.session_id }),
          ...replyTokens.map((token) => JSON.stringify({ type: "token", content: token })),
          JSON.stringify({ type: "done", elapsed: 0.5, ttft: 0.1, tokens: replyTokens.length }),
          "[DONE]",
        ])
      }

      if (path === "/api/agent" && request.method === "POST") {
        const body = (await request.json()) as { session_id: string; message: string; context?: string }
        agentRequests.push(body)
        // Keep the stream open: the permission prompt is answered out of band.
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            permissionStream = controller
            for (const event of [
              JSON.stringify({ type: "start", session_id: body.session_id }),
              JSON.stringify({ type: "run", run_id: "r1", session_id: body.session_id }),
              JSON.stringify({ type: "step_start", content: { step: 1, max_steps: 2 } }),
              JSON.stringify({
                type: "tool_start",
                content: { id: "t1", name: "read_file", args: { path: "README.md" }, step: 1 },
              }),
              JSON.stringify({
                type: "permission_request",
                id: "perm1",
                run_id: "r1",
                tool: "read_file",
                action: "read",
                resources: ["README.md"],
                summary: "read README.md",
                reason: "reading outside the prompt",
                args: { path: "README.md" },
              }),
            ]) {
              controller.enqueue(encoder.encode(`data: ${event}\n\n`))
            }
          },
        })
        return new Response(stream, {
          headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
        })
      }

      if (path.startsWith("/api/permissions/") && request.method === "POST") {
        const body = (await request.json()) as { decision: string }
        permissionStream?.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({
              type: "tool_finish",
              content: { id: "t1", name: "read_file", result: "# Gemma", ok: true },
            })}\n\n`,
          ),
        )
        permissionStream?.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({ type: "text", content: `decision=${body.decision}` })}\n\n`,
          ),
        )
        permissionStream?.enqueue(
          encoder.encode(`data: ${JSON.stringify({ type: "complete", content: "done" })}\n\n`),
        )
        permissionStream?.enqueue(encoder.encode("data: [DONE]\n\n"))
        permissionStream?.close()
        permissionStream = null
        return Response.json({ ok: true, decision: body.decision })
      }

      return Response.json({ error: "not found" }, { status: 404 })
    },
  })

  return {
    base: `http://127.0.0.1:${server.port}`,
    chatRequests,
    agentRequests,
    prepareCalls,
    modeRequests,
    providerModes,
    contextLimits,
    agentSettings,
    permissionConfig,
    stop: () => server.stop(true),
  }
}

async function mount(mock: MockServer): Promise<{ setup: TestRendererSetup; chat: ReturnType<typeof createChat> }> {
  const client = new GemmaClient(mock.base)
  const chat = createChat(client)
  await chat.load()
  await chat.loadModels()
  await chat.loadProviders()
  await chat.loadCommands()

  const setup = await testRender(() => <App chat={chat} />, { width: 110, height: 34 })
  await setup.renderOnce()
  return { setup, chat }
}

let current: MockServer | undefined
let setupRef: TestRendererSetup | undefined

afterEach(async () => {
  await setupRef?.dispose()
  setupRef = undefined
  current?.stop()
  current = undefined
  setTheme("opencode")
  setThemeMode("system")
})

describe("OpenTUI client", () => {
  test("renders the Gemini-style home screen", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    const frame = setup.captureCharFrame()
    // Wordmark + greeting
    expect(frame).toContain("▀")
    expect(frame).toContain("Where should we start?")
    // The prompt pill with real server data
    expect(frame).toContain("Ask anything… (/ for commands)")
    expect(frame).toContain("test-model")
    expect(frame).toContain("llama.cpp")
    expect(frame).toContain("tab mode")
    expect(frame).toContain("ctrl+k models")
    expect(frame).toContain("ctrl+p commands")
    expect(frame).toContain("v0.1.0")
    // No fake strings from the old landing
    expect(frame).not.toContain("Fix broken tests")
    expect(frame).not.toContain("Build")
    expect(frame).not.toContain("Gemma 3 12B Local")
    expect(frame).not.toContain("Ollama")
    expect(frame).not.toContain("shift+tab agents")
    expect(frame).not.toContain("0 MCP")
    expect(frame).not.toContain("/mcps")
    expect(frame).not.toContain("/update")
    // The Gemini rail is visible on the home screen too
    expect(frame).toContain("◆ gemma")
    expect(frame).toContain("New chat")
    expect(frame).toContain("Sessions")
    // The unused Gemini nav items are gone
    expect(frame).not.toContain("Library")
    expect(frame).not.toContain("Scheduled")
    expect(frame).not.toContain("More")
    // Top bar: session title, model and version strip
    expect(frame).toContain("First chat")
  })

  test("the sidebar New chat entry starts a chat", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    const lines = setup.captureCharFrame().split("\n")
    const row = lines.findIndex((line) => line.includes("ctrl+n"))
    const column = lines[row]?.indexOf("New chat") ?? -1
    expect(row).toBeGreaterThanOrEqual(0)
    expect(column).toBeGreaterThanOrEqual(0)

    await setup.mockMouse.click(column + 2, row)
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    expect(chat.activeId()).toBe("s2")
  })

  test("streams a chat reply into the feed", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("hi there")
    setup.mockInput.pressEnter()

    const frame = await setup.waitForFrame((text: string) => text.includes("Hello from the mock"))
    expect(frame).toContain("gemma")
    expect(frame).toContain("hi there")
    expect(frame).toContain("❐ copy")
    expect(frame).toContain("ctx ")
    expect(frame).toContain("tab mode")
    expect(current.chatRequests).toHaveLength(1)
    expect(current.chatRequests[0]?.message).toBe("hi there")
    expect(chat.streaming()).toBe(false)
  })

  test("opens the slash palette as soon as you type a slash", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/")
    const frame = await setup.waitForFrame((text: string) => text.includes("Start a new chat"))
    expect(frame).toContain("/new")
    expect(frame).toContain("↑↓ navigate")
  })

  test("completes a command with Tab", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/cl")
    await setup.waitForFrame((text: string) => text.includes("Clear this chat"))
    setup.mockInput.pressTab()
    await setup.renderOnce()

    expect(setup.captureCharFrame()).toContain("/clear")
  })

  test("surfaces server-only commands in the slash palette", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/co")
    const frame = await setup.waitForFrame((text: string) => text.includes("Compact the conversation"))
    expect(frame).toContain("/compact")
    expect(frame).toContain("/copy")
  })

  test("/history lists saved sessions", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/history")
    setup.mockInput.pressEnter()
    const frame = await setup.waitForFrame((text: string) => text.includes("First chat (0 msg"))
    expect(frame).toContain("First chat (0 msg")
  })

  test("/config shows the live server config", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/config")
    setup.mockInput.pressEnter()
    const frame = await setup.waitForFrame((text: string) => text.includes("model test-model"))
    expect(frame).toContain("provider llama.cpp")
    expect(frame).toContain("temp 0.7")
    expect(frame).toContain("ctx 8,192")
  })

  test("/stats includes the session and message count", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/stats")
    setup.mockInput.pressEnter()
    const frame = await setup.waitForFrame((text: string) => text.includes("0 msg"))
    expect(frame).toContain("ctx 0/8,192")
  })

  test("/sidebar hides and shows the rail", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/sidebar hide")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Sidebar hidden"))
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")

    setup.mockInput.typeText("/sidebar show")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Sidebar shown"))
    expect(setup.captureCharFrame()).toContain("◆ gemma")
  })

  test("/provider switches the provider mode", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/provider native")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Provider mode: native"))
    expect(current.providerModes).toEqual(["native"])

    setup.mockInput.typeText("/provider")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Provider mode: native — usage"))
  })

  test("/context sets and shows the token limit", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/context 16384")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Context limit: 16,384"))
    expect(current.contextLimits).toEqual([16384])

    setup.mockInput.typeText("/context")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Context limit: 16,384 tokens"))
  })

  test("/permissions reads and writes the policy", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/permissions mode ask")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Permission mode set to ask"))
    expect(current.permissionConfig.mode).toBe("ask")

    setup.mockInput.typeText("/permissions allow bash *git push*")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Added rule bash:*git push*:allow"))
    expect(current.permissionConfig.rules).toEqual(["bash:*git push*:allow"])

    setup.mockInput.typeText("/permissions status")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Permission mode: ask · 1 user rules"))

    setup.mockInput.typeText("/permissions reset")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("reset to defaults"))
    expect(current.permissionConfig.rules).toEqual([])
  })

  test("/agent configures dir and steps", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/agent steps 20")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Agent max steps: 20"))
    expect(current.agentSettings).toEqual([{ max_steps: 20 }])

    setup.mockInput.typeText("/agent dir /tmp/project")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Agent workspace: /tmp/project"))
    expect(current.agentSettings[1]).toEqual({ workspace: "/tmp/project" })

    setup.mockInput.typeText("/agent status")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("agent inactive · ws /tmp/project · steps 20"))

    setup.mockInput.typeText("/agent steps 99")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("must be 1..50"))
  })

  test("/mcp, /skills and /lsp report service status", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/mcp")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("files: connected (2 tools)"))

    setup.mockInput.typeText("/mcp reload")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("mcp reload ok"))

    setup.mockInput.typeText("/skills")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("commit: write commits"))

    setup.mockInput.typeText("/skills reload")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("skills reload ok"))

    setup.mockInput.typeText("/lsp")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("pyright (running)"))

    setup.mockInput.typeText("/lsp stop")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("lsp stop ok"))

    setup.mockInput.typeText("/mcp explode")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Usage: /mcp"))
  })

  test("/websearch toggles, checks status and injects context", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    // Search defaults ON, like the classic TUI.
    setup.mockInput.typeText("/websearch status")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Web search ON"))

    setup.mockInput.typeText("latest gemma news")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Hello from the mock"))
    expect(current.chatRequests[0]?.context).toBe("WEB CONTEXT")

    setup.mockInput.typeText("/websearch off")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Web search: OFF"))

    setup.mockInput.typeText("hello again")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Hello from the mock"))
    expect(current.chatRequests[1]?.context).toBeUndefined()

    setup.mockInput.typeText("/websearch test")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Gemma release notes"))

    setup.mockInput.typeText("/websearch url http://127.0.0.1:9999")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("SearXNG URL: http://127.0.0.1:9999"))
  })

  test("/search toggles auto search and the status bar shows the state", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    expect(chat.websearch()).toBe(true)
    setup.mockInput.typeText("/search")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Web search: ON"))

    setup.mockInput.typeText("/search off")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Web search: OFF"))
    expect(chat.websearch()).toBe(false)
    expect((await setup.renderOnce(), setup.captureCharFrame())).toContain("web off")

    setup.mockInput.typeText("/search on")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Web search: ON"))
    expect((await setup.renderOnce(), setup.captureCharFrame())).toContain("web on")
  })

  test("auto search skips smalltalk and searches office questions with sources", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    // "hi" must not even ask the search judge.
    setup.mockInput.typeText("hi")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Hello from the mock"))
    // The stats footer only renders once the turn has fully finished.
    await setup.waitForFrame((text: string) => text.includes("⚡"))
    expect(current.prepareCalls).toEqual([])

    // "write a python loop" asks the server judge, which says no.
    setup.mockInput.typeText("write a python loop")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("write a python loop") && text.split("⚡").length > 2)
    expect(current.prepareCalls).toEqual(["write a python loop"])
    expect(current.chatRequests.every((request) => !request.context)).toBe(true)

    // "who is the cm of tamil nadu" searches and shows its sources.
    setup.mockInput.typeText("who is the cm of tamil nadu")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Hello from the mock") && text.includes("cm of tamil nadu") && text.split("⚡").length > 3)
    expect(current.prepareCalls[1]).toBe("who is the cm of tamil nadu")
    expect(current.chatRequests.at(-1)?.context).toBe("OFFICE CONTEXT")
    const last = chat.activeSession()?.messages.at(-1)
    expect(last?.search?.status).toBe("ok")
    expect(last?.search?.sources?.[0]?.domain).toBe("en.wikipedia.org")
    const frame = await setup.waitForFrame((text: string) =>
      text.includes("List of chief ministers of Tamil Nadu — en.wikipedia.org"),
    )
    expect(frame).toContain("sources · 1")
  })

  test("agent mode injects web context too", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.pressTab()
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    setup.mockInput.typeText("latest gemma news")
    setup.mockInput.pressEnter()
    // The agent request is recorded as soon as the SSE call opens; the mock
    // keeps the stream open, so don't wait for a reply.
    await new Promise((resolve) => setTimeout(resolve, 300))
    await setup.renderOnce()
    expect(current.agentRequests[0]?.context).toBe("WEB CONTEXT")
    const last = chat.activeSession()?.messages.at(-1)
    expect(last?.search?.status).toBe("ok")
  })

  test("/init writes the starter AGENTS.md", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/init")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("AGENTS.md created at /workspace/AGENTS.md"))
  })

  test("polls /health so the header dot knows the connection", async () => {
    current = startMock()
    const { chat } = await mount(current)
    expect(chat.connected()).toBe(true)
  })

  test("Delete removes the active session", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    expect(chat.sessions().some((session) => session.id === "s1")).toBe(true)

    setup.mockInput.pressKey("DELETE")
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    expect(chat.sessions().some((session) => session.id === "s1")).toBe(false)
    expect(chat.activeId()).not.toBe("s1")
  })

  test("/compact folds the session into a checkpoint", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/compact keep the plan")
    setup.mockInput.pressEnter()
    await setup.waitForFrame((text: string) => text.includes("Compacted: 6 messages removed"))
    expect(chat.sessions()[0]?.messages.length).toBe(0)
  })

  test("Tab switches chat and agent mode", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.pressTab()
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    expect(current.modeRequests).toEqual(["agent"])
    expect(chat.mode()).toBe("agent")
    expect(setup.captureCharFrame()).toContain("agent")
  })

  test("opens the provider-grouped model picker with Ctrl+K", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("k", { ctrl: true })
    const frame = await setup.waitForFrame((text: string) => text.includes("Select model"))
    expect(frame).toContain("LLAMA.CPP (LOCAL)")
    expect(frame).toContain("test-model")
    expect(frame).toContain("OLLAMA")
    expect(frame).toContain("qwen2.5-coder")
    expect(frame).toContain("Enter select")
  })

  test("opens the theme picker with Ctrl+T", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("t", { ctrl: true })
    const frame = await setup.waitForFrame((text: string) => text.includes("Theme"))
    expect(frame).toContain("OpenCode")
    expect(frame).toContain("Violet")
    expect(frame).toContain("System")
    expect(frame).toContain("terminal · dark")
    expect(frame).toContain("mode")
  })

  test("cycles system, dark and light in the theme picker", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    expect(activeThemeMode()).toBe("system")
    setup.mockInput.pressKey("t", { ctrl: true })
    await setup.waitForFrame((text: string) => text.includes("Theme"))

    setup.mockInput.pressKey("m")
    await setup.renderOnce()
    expect(activeThemeMode()).toBe("dark")

    setup.mockInput.pressKey("m")
    await setup.renderOnce()
    expect(activeThemeMode()).toBe("light")

    setup.mockInput.pressKey("m")
    await setup.renderOnce()
    expect(activeThemeMode()).toBe("system")
  })

  test("selects the system theme from the picker", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("t", { ctrl: true })
    await setup.waitForFrame((text: string) => text.includes("Theme"))
    for (let index = 0; index < 9; index += 1) setup.mockInput.pressKey("ARROW_DOWN")
    setup.mockInput.pressEnter()
    await setup.renderOnce()

    expect(activeThemeId()).toBe("system")
  })

  test("sets the theme and mode from /theme", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("/theme light")
    setup.mockInput.pressEnter()
    await setup.renderOnce()
    expect(activeThemeMode()).toBe("light")

    setup.mockInput.typeText("/theme violet")
    setup.mockInput.pressEnter()
    await setup.renderOnce()
    expect(activeThemeId()).toBe("violet")
  })

  test("opens help with F1", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("F1")
    const frame = await setup.waitForFrame((text: string) => text.includes("Commands & keys"))
    expect(frame).toContain("toggle sidebar")
    expect(frame).toContain("switch chat / agent")
  })

  test("toggles the sidebar with Ctrl+B in chat", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    // The rail is visible on the home screen from the start.
    expect(setup.captureCharFrame()).toContain("Sessions")
    setup.mockInput.pressKey("b", { ctrl: true })
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("Sessions")
    setup.mockInput.pressKey("b", { ctrl: true })
    await setup.renderOnce()
    expect(setup.captureCharFrame()).toContain("Sessions")
  })

  test("runs leader commands with Ctrl+X", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("x", { ctrl: true })
    await setup.renderOnce()
    expect(setup.captureCharFrame()).toContain("ctrl+x")

    setup.mockInput.pressKey("n")
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()
    expect(chat.sessions()[0]?.id).toBe("s2")
    expect(chat.activeId()).toBe("s2")
  })

  test("mints a browser pairing link with Ctrl+W", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("w", { ctrl: true })
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    expect(setup.captureCharFrame()).toContain("Pair a browser: http://127.0.0.1/pair?token=abc")
  })

  test("runs /clear from the composer", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("hi")
    setup.mockInput.pressEnter()
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()
    expect(chat.activeSession()?.messages).toHaveLength(2)

    setup.mockInput.typeText("/clear")
    await setup.renderOnce()
    setup.mockInput.pressEnter()
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    // The command is handled locally instead of being sent to the model.
    expect(current.chatRequests).toHaveLength(1)
    expect(chat.activeSession()?.messages).toHaveLength(0)
  })

  test("creates a new chat with Ctrl+N", async () => {
    current = startMock()
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.pressKey("n", { ctrl: true })
    // The session is created over HTTP, so give the fetch a real tick to land.
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()
    expect(chat.sessions()[0]?.id).toBe("s2")
    expect(chat.activeId()).toBe("s2")
  })

  test("Ctrl+P opens the slash palette inside a chat", async () => {
    current = startMock()
    const { setup } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("hi")
    setup.mockInput.pressEnter()
    await new Promise((resolve) => setTimeout(resolve, 100))
    await setup.renderOnce()

    setup.mockInput.pressKey("p", { ctrl: true })
    const frame = await setup.waitForFrame((text: string) => text.includes("Start a new chat"))
    expect(frame).toContain("/new")
  })

  test("agent mode streams tool calls and answers a permission prompt", async () => {
    current = startMock(["unused"], { mode: "agent" })
    const { setup, chat } = await mount(current)
    setupRef = setup

    setup.mockInput.typeText("inspect the readme")
    setup.mockInput.pressEnter()

    const prompt = await setup.waitForFrame((text: string) => text.includes("Permission required"))
    expect(prompt).toContain("read_file")
    expect(prompt).toContain("read README.md")
    expect(prompt).toContain("Allow once")

    setup.mockInput.pressKey("y")
    const frame = await setup.waitForFrame((text: string) => text.includes("decision=allow"))
    expect(frame).toContain("read_file")
    expect(frame).toContain("# Gemma")
    expect(frame).not.toContain("Permission required")

    expect(current.agentRequests).toHaveLength(1)
    expect(chat.permission()).toBeNull()
    expect(chat.streaming()).toBe(false)
  })
})
