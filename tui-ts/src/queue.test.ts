import { describe, expect, test } from "bun:test"
import { GemmaClient } from "./api"
import { createChat } from "./chat"

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

describe("Message Queue in chat.ts", () => {
  test("queues messages when busy and drains automatically FIFO on idle", async () => {
    const received: string[] = []
    let finishFirstReply: () => void = () => {}

    const server = Bun.serve({
      port: 0,
      async fetch(request) {
        const url = new URL(request.url)
        if (url.pathname === "/api/info") {
          return Response.json({ mode: "chat", provider: "llama.cpp", context_max: 8192 })
        }
        if (url.pathname === "/health") {
          return Response.json({ status: "ok" })
        }
        if (url.pathname === "/api/sessions" && request.method === "POST") {
          return Response.json({ id: "s1" }, { status: 201 })
        }
        if (url.pathname === "/api/chat") {
          const body = (await request.json()) as { message: string }
          received.push(body.message)

          if (body.message === "first message") {
            const encoder = new TextEncoder()
            const bodyStream = new ReadableStream<Uint8Array>({
              start(controller) {
                controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "start", session_id: "s1" })}\n\n`))
                controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "token", content: "Working..." })}\n\n`))
                finishFirstReply = () => {
                  controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "done", elapsed: 0.1 })}\n\n`))
                  controller.enqueue(encoder.encode(`data: [DONE]\n\n`))
                  controller.close()
                }
              },
            })
            return new Response(bodyStream, {
              headers: { "Content-Type": "text/event-stream" },
            })
          }

          return sseResponse([
            JSON.stringify({ type: "start", session_id: "s1" }),
            JSON.stringify({ type: "token", content: `Echo: ${body.message}` }),
            JSON.stringify({ type: "done", elapsed: 0.1 }),
            "[DONE]",
          ])
        }
        return Response.json({ error: "not found" }, { status: 404 })
      },
    })

    try {
      const client = new GemmaClient(`http://127.0.0.1:${server.port}`)
      const chat = createChat(client)
      await chat.newChat()

      // 1. Send first message
      const firstPromise = chat.send("first message")
      await new Promise((r) => setTimeout(r, 50))
      expect(chat.streaming()).toBe(true)
      expect(received).toEqual(["first message"])

      // 2. Queue second and third messages while streaming
      await chat.send("second message")
      await chat.send("third message")

      expect(chat.messageQueue().length).toBe(2)
      expect(chat.messageQueue()[0]?.text).toBe("second message")
      expect(chat.messageQueue()[1]?.text).toBe("third message")
      expect(received).toEqual(["first message"])

      // 3. Complete first reply; queue should drain sequentially
      finishFirstReply()
      await firstPromise

      // Wait for queue drain microtask and processing
      for (let i = 0; i < 50; i++) {
        if (received.length === 3 && chat.messageQueue().length === 0) break
        await new Promise((r) => setTimeout(r, 20))
      }

      expect(received).toEqual(["first message", "second message", "third message"])
      expect(chat.messageQueue().length).toBe(0)
    } finally {
      server.stop(true)
    }
  })

  test("allows removing individual queued items and clearing entire queue", async () => {
    const client = new GemmaClient("http://127.0.0.1:9999")
    const chat = createChat(client)

    chat.queueMessage("item 1")
    chat.queueMessage("item 2")
    chat.queueMessage("item 3")

    expect(chat.messageQueue().length).toBe(3)
    const item2Id = chat.messageQueue()[1]?.id

    chat.dequeueMessage(item2Id)
    expect(chat.messageQueue().length).toBe(2)
    expect(chat.messageQueue().map((m) => m.text)).toEqual(["item 1", "item 3"])

    chat.clearQueue()
    expect(chat.messageQueue().length).toBe(0)
  })

  test("stop (Ctrl+C / abort) cancels active turn and flushes the queue", async () => {
    let aborted = false
    const server = Bun.serve({
      port: 0,
      async fetch(request) {
        const url = new URL(request.url)
        if (url.pathname === "/api/info") {
          return Response.json({ mode: "chat", provider: "llama.cpp", context_max: 8192 })
        }
        if (url.pathname === "/api/sessions" && request.method === "POST") {
          return Response.json({ id: "s1" }, { status: 201 })
        }
        if (url.pathname === "/api/chat") {
          const encoder = new TextEncoder()
          request.signal.addEventListener("abort", () => {
            aborted = true
          })
          const bodyStream = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "start", session_id: "s1" })}\n\n`))
            },
          })
          return new Response(bodyStream, {
            headers: { "Content-Type": "text/event-stream" },
          })
        }
        return Response.json({})
      },
    })

    try {
      const client = new GemmaClient(`http://127.0.0.1:${server.port}`)
      const chat = createChat(client)
      await chat.newChat()

      const turn = chat.send("running task")
      await new Promise((r) => setTimeout(r, 50))
      expect(chat.streaming()).toBe(true)

      // Queue an item
      chat.queueMessage("pending item")
      expect(chat.messageQueue().length).toBe(1)

      // User hits Ctrl+C / abort
      chat.stop()
      await turn

      expect(chat.messageQueue().length).toBe(0)
      expect(chat.streaming()).toBe(false)
      const assistant = chat.activeSession()?.messages.find((m) => m.role === "assistant")
      expect(assistant?.stopped).toBe(true)
    } finally {
      server.stop(true)
    }
  })
})
