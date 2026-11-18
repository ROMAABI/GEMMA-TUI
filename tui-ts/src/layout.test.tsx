/** Layout constants and shell sizing at the supported terminal widths. */

import { describe, expect, test } from "bun:test"
import { mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { testRender } from "@opentui/solid"
import type { TestRendererSetup } from "@opentui/core/testing"
import { App } from "./app"
import { createChat } from "./chat"
import { LAYOUT, SIDEBAR_HIDE_BELOW, SIDEBAR_WIDTH, sessionTitleWidth } from "./layout"
import { truncate } from "./format"

describe("sidebar layout", () => {
  test("hide threshold is a single layout constant", () => {
    expect(SIDEBAR_HIDE_BELOW).toBe(120)
  })

  test("width follows the single breakpoint config", () => {
    expect(SIDEBAR_WIDTH(140)).toBe(LAYOUT.sidebar.columns.wide)
    expect(SIDEBAR_WIDTH(120)).toBe(36)
    expect(SIDEBAR_WIDTH(119)).toBe(32)
    expect(SIDEBAR_WIDTH(100)).toBe(32)
    expect(SIDEBAR_WIDTH(99)).toBe(28)
    expect(SIDEBAR_WIDTH(80)).toBe(28)
  })

  test("title budget grows with the rail and only truncates long titles", () => {
    expect(sessionTitleWidth(120)).toBe(26)
    expect(sessionTitleWidth(110)).toBe(22)
    expect(sessionTitleWidth(80)).toBe(18)
    const budget = sessionTitleWidth(120)
    expect(truncate("Which OS I Am Using", budget)).toBe("Which OS I Am Using")
    const long = truncate("who is the cm of tamil nadu and what is the weather", budget)
    expect(long.length).toBe(budget)
    expect(long.endsWith("…")).toBe(true)
  })

  const setups: TestRendererSetup[] = []
  const sizes: Array<[number, number]> = [
    [120, 40],
    [90, 30],
    [72, 24],
    [60, 20],
  ]
  for (const [width, height] of sizes) {
    test(`shell has no overflow at ${width}x${height}`, async () => {
      const chat = createChat({
        health: async () => ({ status: "ok", app: "gemma" }),
      } as never)
      const setup = await testRender(() => <App chat={chat} />, { width, height })
      setups.push(setup)
      await setup.renderOnce()
      const lines = setup.captureCharFrame().split("\n").filter((line, index) =>
        index < height || line.length > 0,
      )
      expect(lines.length).toBe(height)
      for (const line of lines) expect(line.length).toBe(width)
      // The rail is shown at the threshold and above, hidden below it.
      expect(setup.captureCharFrame().includes("◆ gemma")).toBe(width >= SIDEBAR_HIDE_BELOW)
      if (width < SIDEBAR_HIDE_BELOW) {
        expect(setup.captureCharFrame()).toContain("ctrl+b sessions")
        expect(setup.captureCharFrame()).toContain("Where should we start?")
      }
    })
  }

  test("live resize follows the threshold in both directions", async () => {
    const chat = createChat({ health: async () => ({ status: "ok", app: "gemma" }) } as never)
    const setup = await testRender(() => <App chat={chat} />, { width: 120, height: 40 })
    setups.push(setup)
    await setup.renderOnce()
    expect(setup.captureCharFrame()).toContain("◆ gemma")

    setup.resize(SIDEBAR_HIDE_BELOW - 1, 24)
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")

    setup.resize(SIDEBAR_HIDE_BELOW, 24)
    await setup.renderOnce()
    expect(setup.captureCharFrame()).toContain("◆ gemma")
  })

  test("manual ctrl+b choice survives resizes; narrow rail floats and Esc closes it", async () => {
    const chat = createChat({ health: async () => ({ status: "ok", app: "gemma" }) } as never)
    const setup = await testRender(() => <App chat={chat} />, { width: 120, height: 40 })
    setups.push(setup)
    await setup.renderOnce()

    setup.mockInput.pressKey("b", { ctrl: true })
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")

    setup.resize(60, 20)
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")

    setup.resize(120, 40)
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")

    // Opening it while narrow overlays the chat; Esc dismisses it again.
    setup.resize(60, 20)
    await setup.renderOnce()
    setup.mockInput.pressKey("b", { ctrl: true })
    await setup.renderOnce()
    expect(setup.captureCharFrame()).toContain("◆ gemma")
    setup.mockInput.pressKey("ESCAPE")
    // A lone ESC is released by the parser after its escape timeout.
    await new Promise((resolve) => setTimeout(resolve, 150))
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")
  })

  test("selecting a session from the narrow overlay closes it", async () => {
    const now = "2026-11-18T10:00:00Z"
    const chat = createChat({
      health: async () => ({ status: "ok", app: "gemma" }),
      info: async () => ({ app: "gemma", mode: "chat", model: "test", version: "0.1.0" }),
      sessions: async () => [
        { id: "s1", title: "Old chat", updated_at: now, message_count: 1 },
      ],
      session: async () => ({
        id: "s1",
        title: "Old chat",
        updated_at: now,
        messages: [{ role: "user", content: "hi", created_at: now }],
      }),
    } as never)
    await chat.load()
    const setup = await testRender(() => <App chat={chat} />, { width: 60, height: 20 })
    setups.push(setup)
    await setup.renderOnce()
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")

    setup.mockInput.pressKey("b", { ctrl: true })
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    expect(frame).toContain("◆ gemma")
    const row = frame.split("\n").findIndex((line) => line.includes("Old chat"))
    expect(row).toBeGreaterThanOrEqual(0)
    await setup.mockMouse.click(2, row)
    await setup.renderOnce()
    expect(chat.activeId()).toBe("s1")
    expect(setup.captureCharFrame()).not.toContain("◆ gemma")
  })

  test("narrow composer uses the short placeholder on one line", async () => {
    const chat = createChat({ health: async () => ({ status: "ok", app: "gemma" }) } as never)
    const setup = await testRender(() => <App chat={chat} />, { width: 70, height: 24 })
    setups.push(setup)
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    expect(frame).toContain("Ask anything…")
    expect(frame).not.toContain("(/ for commands)")
    expect(frame.split("\n").some((line) => line.includes("⌄") && line.includes("➤"))).toBe(true)
    expect(frame).toContain("Where should we start?")
  })

  test("resize log tracks live columns across the 120-col threshold", async () => {
    const logPath = join(mkdtempSync(join(tmpdir(), "gemma-layout-")), "tui-debug.log")
    const previous = process.env.GEMMA_TUI_DEBUG_LOG
    process.env.GEMMA_TUI_DEBUG_LOG = logPath
    try {
      const chat = createChat({ health: async () => ({ status: "ok", app: "gemma" }) } as never)
      const setup = await testRender(() => <App chat={chat} />, { width: 200, height: 40 })
      setups.push(setup)
      await setup.renderOnce()
      for (const width of [119, 90, 70, 120]) {
        setup.resize(width, 30)
        await setup.renderOnce()
      }
      const lines = readFileSync(logPath, "utf8").split("\n").filter((line) => line.includes("cols="))
      for (const [cols, state] of [
        [200, "shown"], [119, "hidden"], [90, "hidden"], [70, "hidden"], [120, "shown"],
      ] as Array<[number, string]>) {
        expect(lines.some((line) => line.includes(`cols=${cols} sidebar=${state}`))).toBe(true)
      }
    } finally {
      if (previous === undefined) delete process.env.GEMMA_TUI_DEBUG_LOG
      else process.env.GEMMA_TUI_DEBUG_LOG = previous
    }
  })

  test("disposes renderers", async () => {
    for (const setup of setups) await setup.dispose()
  })
})
