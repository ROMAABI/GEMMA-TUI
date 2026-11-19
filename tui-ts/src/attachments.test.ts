/** Attachment helpers and composer state. */

import { describe, expect, test } from "bun:test"
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import {
  attachmentIcon, attachmentLabel, extractPaths, formatBytes, formatDuration, kittySupported,
} from "./attachments"
import { createChat } from "./chat"

describe("attachments", () => {
  test("extracts quoted, escaped and file:// paths", () => {
    const dir = mkdtempSync(join(tmpdir(), "gemma-attach-"))
    const file = join(dir, "photo.png")
    writeFileSync(file, "x")
    const quoted = extractPaths(`see "${file}" please`)
    const uri = extractPaths(`file://${file}`)
    const bare = extractPaths(`attach ${file} now`)
    expect(quoted).toContain(file)
    expect(uri).toContain(file)
    expect(bare).toContain(file)
  })

  test("formats chips", () => {
    expect(formatBytes(1_500_000)).toBe("1.4MB")
    expect(formatDuration(12)).toBe("0:12")
    expect(attachmentIcon("video")).toBe("🎞")
    expect(attachmentLabel({ id: "1", name: "clip.mp4", kind: "video", size: 5, stored_path: "", duration: 12, frames: 8 }))
      .toBe("🎞 clip.mp4 0:12 · 8 frames")
  })

  test("kitty detection uses terminal env", () => {
    const previous = process.env.TERM
    process.env.TERM = "xterm-kitty"
    expect(kittySupported()).toBe(true)
    if (previous === undefined) delete process.env.TERM
    else process.env.TERM = previous
  })

  test("chat stores and clears attachments", async () => {
    const calls: string[] = []
    const client = {
      health: async () => ({ status: "ok", app: "gemma" }),
      info: async () => ({ app: "gemma", mode: "chat", model: "m", version: "1", context_max: 8192 }),
      sessions: async () => [],
      session: async (id: string) => ({ id, title: "New chat", updated_at: "", messages: [] }),
      prepareAttachment: async (path: string) => {
        calls.push(path)
        return { ok: true, attachment: { id: "a1", name: "photo.png", kind: "image", size: 100, stored_path: path } }
      },
    }
    const chat = createChat(client as never)
    await chat.load()
    await chat.addAttachment("/tmp/photo.png")
    expect(chat.attachments().length).toBe(1)
    chat.removeAttachment("a1")
    expect(chat.attachments().length).toBe(0)
    expect(calls).toEqual(["/tmp/photo.png"])
  })
})
