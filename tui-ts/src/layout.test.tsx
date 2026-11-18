/** Layout constants and shell sizing at the supported terminal widths. */

import { describe, expect, test } from "bun:test"
import { testRender } from "@opentui/solid"
import type { TestRendererSetup } from "@opentui/core/testing"
import { App } from "./app"
import { createChat } from "./chat"
import { LAYOUT, SIDEBAR_WIDTH, sessionTitleWidth } from "./layout"
import { truncate } from "./format"

describe("sidebar layout", () => {
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
    [100, 30],
    [80, 24],
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
      // The rail is shown at >= 100 columns and auto-hidden below 92.
      expect(setup.captureCharFrame().includes("◆ gemma")).toBe(width >= 100)
    })
  }

  test("disposes renderers", async () => {
    for (const setup of setups) await setup.dispose()
  })
})
