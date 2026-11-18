/** Startup should always open an empty New chat, never the last conversation. */

import { describe, expect, test } from "bun:test"
import { createChat } from "./chat"

const NOW = "2026-11-18T10:00:00Z"

function fakeClient(options: {
  sessions?: Array<{ id: string; title: string; messages: Array<{ role: string; content: string }> }>
  createdId?: string
} = {}) {
  const calls = { createSession: 0 }
  const sessions = options.sessions ?? []
  const client = {
    calls,
    info: async () => ({ app: "gemma", mode: "chat", model: "test", version: "0.1.0" }),
    health: async () => ({ status: "ok", app: "gemma" }),
    sessions: async () =>
      sessions.map((session) => ({
        id: session.id,
        title: session.title,
        updated_at: NOW,
        message_count: session.messages.length,
      })),
    session: async (id: string) => {
      const found = sessions.find((session) => session.id === id)
      return {
        id,
        title: found?.title ?? "New chat",
        updated_at: NOW,
        messages: (found?.messages ?? []).map((message, index) => ({
          ...message,
          created_at: NOW,
        })),
      }
    },
    createSession: async () => {
      calls.createSession += 1
      return options.createdId ?? "fresh"
    },
    streamChat: async (
      _id: string,
      _message: string,
      handlers: { onToken: (token: string) => void; onDone?: (stats: unknown) => void },
    ) => {
      handlers.onToken("Hi")
      handlers.onDone?.({ elapsed: 0.2, ttft: 0.1, tokens: 1 })
    },
  }
  return client
}

describe("startup session", () => {
  test("load selects the empty session and creates no DB row", async () => {
    const client = fakeClient({
      sessions: [
        { id: "old", title: "Old chat", messages: [{ role: "user", content: "hello" }] },
        { id: "empty", title: "New chat", messages: [] },
      ],
    })
    const chat = createChat(client as never)
    await chat.load()
    expect(chat.activeId()).toBe("empty")
    expect(chat.activeSession()?.messages).toHaveLength(0)
    expect(client.calls.createSession).toBe(0)
    expect(chat.sessions().some((session) => session.id === "old")).toBe(true)
  })

  test("load with no sessions keeps a local placeholder", async () => {
    const client = fakeClient()
    const chat = createChat(client as never)
    await chat.load()
    expect(chat.activeId()).toMatch(/^local-/)
    expect(chat.sessions()).toHaveLength(1)
    expect(client.calls.createSession).toBe(0)
  })

  test("new chat reuses an existing empty session", async () => {
    const client = fakeClient({
      sessions: [{ id: "empty", title: "New chat", messages: [] }],
    })
    const chat = createChat(client as never)
    await chat.load()
    await chat.newChat()
    expect(chat.activeId()).toBe("empty")
    expect(chat.sessions()).toHaveLength(1)
    expect(client.calls.createSession).toBe(0)
  })

  test("the DB row is created only on the first send", async () => {
    const client = fakeClient({ createdId: "real1" })
    const chat = createChat(client as never)
    await chat.load()
    chat.setWebsearch(false)
    const placeholder = chat.activeId()
    expect(placeholder).toMatch(/^local-/)

    await chat.send("hello")
    expect(client.calls.createSession).toBe(1)
    expect(chat.activeId()).toBe("real1")
    expect(chat.activeSession()?.messages.length).toBe(2)
  })
})
