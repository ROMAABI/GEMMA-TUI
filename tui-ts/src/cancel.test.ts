/** Cancellation must mark the turn stopped, not surface a generic error. */

import { expect, test } from "bun:test"
import { createChat } from "./chat"

function fakeClient() {
  return {
    health: async () => ({ status: "ok", app: "gemma" }),
    info: async () => ({ app: "gemma", mode: "agent", model: "m", version: "1", context_max: 8192 }),
    sessions: async () => [],
    session: async (id: string) => ({ id, title: "New chat", updated_at: "", messages: [] }),
    createSession: async () => "s1",
    abortRun: async () => ({ ok: true }),
    streamAgent: async (
      _sid: string,
      _message: string,
      _handlers: unknown,
      signal?: AbortSignal,
    ) => {
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new Error("connection closed")))
      })
    },
  }
}

test("stop() keeps the partial chat reply and the rest of the transcript", async () => {
  const client = {
    ...fakeClient(),
    info: async () => ({ app: "gemma", mode: "chat", model: "m", version: "1", context_max: 8192 }),
    streamChat: async (
      _sid: string,
      _message: string,
      handlers: { onToken?: (token: string) => void },
      signal?: AbortSignal,
    ) => {
      handlers.onToken?.("Half of the answer")
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))
      })
    },
  }
  const chat = createChat(client as never)
  await chat.load()
  chat.setWebsearch(false)

  const running = chat.send("hello")
  await new Promise((resolve) => setTimeout(resolve, 20))
  chat.stop()
  await running

  const messages = chat.activeSession()?.messages ?? []
  const assistant = messages.find((message) => message.role === "assistant")
  expect(messages.some((message) => message.role === "user" && message.content === "hello")).toBe(true)
  expect(assistant?.content).toContain("Half of the answer")
  expect(assistant?.stopped).toBe(true)
})

test("stop() marks an agent turn as stopped even on a non-AbortError", async () => {
  const chat = createChat(fakeClient() as never)
  await chat.load()
  chat.setWebsearch(false)

  const running = chat.send("do some work")
  await new Promise((resolve) => setTimeout(resolve, 20))
  chat.stop()
  await running

  const assistant = chat.activeSession()?.messages.find((message) => message.role === "assistant")
  expect(assistant?.stopped).toBe(true)
  expect(assistant?.error).toBeUndefined()
  expect(chat.streaming()).toBe(false)
})
