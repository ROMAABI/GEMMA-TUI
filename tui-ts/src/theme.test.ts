import { describe, expect, test } from "bun:test"
import {
  DARK_PALETTE,
  LIGHT_PALETTE,
  THEMES,
  modeFromBackground,
  resolveTheme,
  terminalFromColors,
  type TerminalPalette,
} from "./theme"

const EMPTY: TerminalPalette = { mode: null, background: null, foreground: null, colors: [] }

const TERMINAL: TerminalPalette = {
  mode: "dark",
  background: "#101418",
  foreground: "#d8dee9",
  colors: [
    "#1b1f23", "#d06c75", "#8fbf7f", "#d6b26a", "#6f9fd8", "#b48ead", "#63b3b3", "#c8ccd4",
    "#4b5263", "#e06c75", "#a3d9a5", "#e5c07b", "#82aaff", "#c792ea", "#89ddff", "#ffffff",
  ],
}

describe("theme resolution", () => {
  test("built-ins expose dark and light variants", () => {
    expect(THEMES.map((theme) => theme.id)).toEqual(["opencode", "violet", "cyan", "emerald", "system"])
    for (const theme of THEMES.filter((candidate) => !candidate.system)) {
      expect(theme.dark.palette).toBe(DARK_PALETTE)
      expect(theme.light.palette).toBe(LIGHT_PALETTE)
    }
  })

  test("explicit modes pick the matching variant", () => {
    const dark = resolveTheme("violet", "dark", EMPTY)
    const light = resolveTheme("violet", "light", EMPTY)

    expect(dark.mode).toBe("dark")
    expect(dark.accent).toBe("#a78bfa")
    expect(dark.colors.page).toBe("#0a0a0a")

    expect(light.mode).toBe("light")
    expect(light.accent).toBe("#6d4bd8")
    expect(light.colors.page).toBe("#ffffff")
    expect(light.colors.text).toBe("#1c1c1c")
  })

  test("system mode follows the terminal appearance", () => {
    expect(resolveTheme("opencode", "system", { ...EMPTY, mode: "light" }).mode).toBe("light")
    expect(resolveTheme("opencode", "system", { ...EMPTY, mode: "light" }).colors.page).toBe("#ffffff")
    expect(resolveTheme("opencode", "system", EMPTY).mode).toBe("dark")
  })

  test("the system theme builds its palette from terminal colors", () => {
    const resolved = resolveTheme("system", "system", TERMINAL)

    expect(resolved.id).toBe("system")
    expect(resolved.mode).toBe("dark")
    expect(resolved.colors.page).toBe("#101418")
    expect(resolved.colors.text).toBe("#d8dee9")
    expect(resolved.accent).toBe("#63b3b3")
    expect(resolved.colors.danger).toBe("#d06c75")
    expect(resolved.colors.success).toBe("#8fbf7f")
    // Surfaces are blended from the terminal background toward its foreground.
    expect(resolved.colors.card).not.toBe(resolved.colors.page)
  })

  test("the system theme falls back to terminal defaults without a palette", () => {
    const resolved = resolveTheme("system", "system", EMPTY)
    expect(resolved.id).toBe("system")
    expect(resolved.mode).toBe("dark")
    // RGBA defaults, not gemma's near-black canvas.
    expect(resolved.colors.page).not.toBe("#0a0a0a")
  })

  test("terminal colors infer light and dark modes", () => {
    expect(modeFromBackground("#ffffff")).toBe("light")
    expect(modeFromBackground("#0a0a0a")).toBe("dark")
    expect(modeFromBackground(null)).toBeNull()
  })

  test("normalizes the renderer palette result", () => {
    const detected = terminalFromColors({
      palette: TERMINAL.colors,
      defaultForeground: "#d8dee9",
      defaultBackground: "#fafafa",
      cursorColor: null,
      mouseForeground: null,
      mouseBackground: null,
      tekForeground: null,
      tekBackground: null,
      highlightBackground: null,
      highlightForeground: null,
    })

    expect(detected.mode).toBe("light")
    expect(detected.background).toBe("#fafafa")
    expect(detected.colors[6]).toBe("#63b3b3")
    expect(terminalFromColors(null)).toEqual(EMPTY)
  })
})
