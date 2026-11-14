/**
 * Theme system, modeled on OpenCode's `theme.name` + `theme.mode`:
 *
 * - Every theme carries a complete token palette with `dark` and `light`
 *   variants (page, surfaces, borders, text, feedback colors).
 * - `theme.mode` is `system | dark | light`; `system` follows the terminal's
 *   own light/dark appearance.
 * - The `system` theme builds its palette from the terminal's colors (the
 *   same idea as OpenCode's "system" theme, which is only available when the
 *   terminal palette can be read).
 *
 * `colors()` is the live palette. Components read it while rendering, so a
 * theme or mode change repaints the whole UI.
 */

import { createMemo, createSignal } from "solid-js"
import { RGBA, type CliRenderer, type ColorInput, type TerminalColors } from "@opentui/core"

export type ThemeMode = "system" | "dark" | "light"
export type ResolvedMode = "dark" | "light"

/** The complete set of colors the UI paints with. */
export interface Palette {
  page: ColorInput
  panel: ColorInput
  card: ColorInput
  element: ColorInput
  border: ColorInput
  borderSoft: ColorInput
  text: ColorInput
  dim: ColorInput
  muted: ColorInput
  faint: ColorInput
  code: ColorInput
  danger: ColorInput
  success: ColorInput
  warning: ColorInput
  info: ColorInput
}

export interface ThemeVariant {
  accent: ColorInput
  accentDim: ColorInput
  palette: Palette
}

export interface Theme {
  id: string
  name: string
  description: string
  dark: ThemeVariant
  light: ThemeVariant
  /** Palette comes from the terminal rather than a fixed color set. */
  system?: boolean
}

/** A theme with its mode resolved: what the components actually render. */
export interface ResolvedTheme {
  id: string
  name: string
  mode: ResolvedMode
  accent: ColorInput
  accentDim: ColorInput
  colors: Palette
}

/** Terminal colors detected with OSC queries (all values are hex or null). */
export interface TerminalPalette {
  mode: ResolvedMode | null
  background: string | null
  foreground: string | null
  colors: Array<string | null>
}

const EMPTY_TERMINAL: TerminalPalette = {
  mode: null,
  background: null,
  foreground: null,
  colors: [],
}

export const DARK_PALETTE: Palette = {
  page: "#0a0a0a",
  panel: "#101010",
  card: "#161616",
  element: "#1e1e1e",
  border: "#262626",
  borderSoft: "#1b1b1b",
  text: "#eeeeee",
  dim: "#a0a0a0",
  muted: "#7a7a7a",
  faint: "#565656",
  code: "#0d0d0d",
  danger: "#e06c75",
  success: "#7fd88f",
  warning: "#f5a742",
  info: "#5c9cf5",
}

export const LIGHT_PALETTE: Palette = {
  page: "#ffffff",
  panel: "#f6f6f6",
  card: "#efefef",
  element: "#e6e6e6",
  border: "#d6d6d6",
  borderSoft: "#e6e6e6",
  text: "#1c1c1c",
  dim: "#565656",
  muted: "#6f6f6f",
  faint: "#9a9a9a",
  code: "#f4f4f4",
  danger: "#c0392b",
  success: "#2f8f46",
  warning: "#a86400",
  info: "#2f6fd0",
}

function variant(accent: string, accentDim: string, palette: Palette): ThemeVariant {
  return { accent, accentDim, palette }
}

/**
 * Fallback for the `system` theme when the terminal palette cannot be read:
 * ANSI slots plus the terminal's own default foreground/background, so the UI
 * still inherits the terminal instead of forcing gemma's colors.
 */
const SYSTEM_FALLBACK: ThemeVariant = {
  accent: RGBA.fromIndex(6),
  accentDim: RGBA.fromIndex(8),
  palette: {
    page: RGBA.defaultBackground(),
    panel: RGBA.defaultBackground(),
    card: RGBA.defaultBackground(),
    element: RGBA.fromIndex(8),
    border: RGBA.fromIndex(8),
    borderSoft: RGBA.fromIndex(8),
    text: RGBA.defaultForeground(),
    dim: RGBA.fromIndex(7),
    muted: RGBA.fromIndex(8),
    faint: RGBA.fromIndex(8),
    code: RGBA.defaultBackground(),
    danger: RGBA.fromIndex(1),
    success: RGBA.fromIndex(2),
    warning: RGBA.fromIndex(3),
    info: RGBA.fromIndex(4),
  },
}

export const THEMES: Theme[] = [
  {
    id: "opencode",
    name: "OpenCode",
    description: "warm amber on near-black",
    dark: variant("#fab283", "#7a4a2b", DARK_PALETTE),
    light: variant("#b5541d", "#f0cdb4", LIGHT_PALETTE),
  },
  {
    id: "violet",
    name: "Violet",
    description: "soft purple, low glare",
    dark: variant("#a78bfa", "#5b3fa0", DARK_PALETTE),
    light: variant("#6d4bd8", "#d3c6f8", LIGHT_PALETTE),
  },
  {
    id: "cyan",
    name: "Cyan",
    description: "cool teal on charcoal",
    dark: variant("#56b6c2", "#2c6570", DARK_PALETTE),
    light: variant("#1f7f8c", "#b3dbe1", LIGHT_PALETTE),
  },
  {
    id: "emerald",
    name: "Emerald",
    description: "muted green on graphite",
    dark: variant("#7fd88f", "#33663d", DARK_PALETTE),
    light: variant("#2f8f4a", "#b6dfc1", LIGHT_PALETTE),
  },
  {
    id: "system",
    name: "System",
    description: "follows your terminal palette",
    system: true,
    dark: SYSTEM_FALLBACK,
    light: SYSTEM_FALLBACK,
  },
]

/** Static palette for code paths that run before the theme store is ready. */
export const COLORS = DARK_PALETTE

// ── Color math ────────────────────────────────────────────────────────────

function hexToRgb(hex: string): [number, number, number] | null {
  const clean = hex.replace(/^#/, "")
  if (!/^[0-9a-fA-F]{6}$/.test(clean)) return null
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ]
}

function rgbToHex(rgb: [number, number, number]): string {
  return `#${rgb.map((value) => Math.round(value).toString(16).padStart(2, "0")).join("")}`
}

/** Blend `to` into `from`; `amount` is 0 (all from) to 1 (all to). */
export function tint(from: string, to: string, amount: number): string {
  const a = hexToRgb(from)
  const b = hexToRgb(to)
  if (!a || !b) return from
  const t = Math.max(0, Math.min(1, amount))
  return rgbToHex([
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ])
}

function luminance(hex: string): number {
  const rgb = hexToRgb(hex)
  if (!rgb) return 0
  return (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
}

export function modeFromBackground(background: string | null): ResolvedMode | null {
  if (!background) return null
  return luminance(background) > 128 ? "light" : "dark"
}

// ── System theme ──────────────────────────────────────────────────────────

function systemVariant(terminal: TerminalPalette): ThemeVariant {
  const background = terminal.background
  const foreground = terminal.foreground
  if (!background || !foreground) return SYSTEM_FALLBACK

  const pick = (index: number, fallback: string) => terminal.colors[index] ?? fallback
  const accent = pick(6, "#56b6c2")

  return {
    accent,
    accentDim: tint(accent, background, 0.55),
    palette: {
      page: background,
      panel: tint(background, foreground, 0.05),
      card: tint(background, foreground, 0.08),
      element: tint(background, foreground, 0.12),
      border: tint(background, foreground, 0.2),
      borderSoft: tint(background, foreground, 0.1),
      text: foreground,
      dim: tint(foreground, background, 0.2),
      muted: tint(foreground, background, 0.42),
      faint: tint(foreground, background, 0.58),
      code: tint(background, foreground, 0.04),
      danger: pick(1, "#e06c75"),
      success: pick(2, "#7fd88f"),
      warning: pick(3, "#f5a742"),
      info: pick(4, "#5c9cf5"),
    },
  }
}

export function resolveTheme(id: string, mode: ThemeMode, terminal: TerminalPalette): ResolvedTheme {
  const theme = THEMES.find((candidate) => candidate.id === id) ?? THEMES[0]!
  const detected = terminal.mode
  const resolved: ResolvedMode = mode === "system" ? (detected ?? "dark") : mode

  if (theme.system) {
    const colors = systemVariant(terminal)
    return {
      id: theme.id,
      name: theme.name,
      mode: detected ?? resolved,
      accent: colors.accent,
      accentDim: colors.accentDim,
      colors: colors.palette,
    }
  }

  const colors = resolved === "light" ? theme.light : theme.dark
  return {
    id: theme.id,
    name: theme.name,
    mode: resolved,
    accent: colors.accent,
    accentDim: colors.accentDim,
    colors: colors.palette,
  }
}

// ── Live theme state ──────────────────────────────────────────────────────

const [themeId, setThemeId] = createSignal("opencode")
const [themeMode, setThemeModeSignal] = createSignal<ThemeMode>("system")
const [terminal, setTerminal] = createSignal<TerminalPalette>(EMPTY_TERMINAL)

/** The resolved theme the whole UI renders with. */
export const theme = createMemo<ResolvedTheme>(() => resolveTheme(themeId(), themeMode(), terminal()))

/** The live palette, for components. */
export const colors = (): Palette => theme().colors

export const activeThemeId = (): string => themeId()
export const activeThemeMode = (): ThemeMode => themeMode()
export const terminalPalette = (): TerminalPalette => terminal()

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === "system" || value === "dark" || value === "light"
}

export function setTheme(id: string): void {
  if (THEMES.some((candidate) => candidate.id === id)) setThemeId(id)
}

export function setThemeMode(mode: ThemeMode): void {
  setThemeModeSignal(mode)
}

export function cycleThemeMode(delta = 1): ThemeMode {
  const modes: ThemeMode[] = ["system", "dark", "light"]
  const index = modes.indexOf(themeMode())
  const next = modes[(index + delta + modes.length) % modes.length]!
  setThemeModeSignal(next)
  return next
}

/** Next theme in the picker order, used by the Ctrl+X T shortcut path. */
export function nextThemeId(): string {
  const index = THEMES.findIndex((candidate) => candidate.id === themeId())
  return THEMES[(index + 1) % THEMES.length]!.id
}

/** Resolve any theme for previews (the picker swatches). */
export function previewTheme(id: string, mode: ThemeMode): ResolvedTheme {
  return resolveTheme(id, mode, terminal())
}

/** Normalize the renderer's OSC palette result. */
export function terminalFromColors(colors: TerminalColors | null): TerminalPalette {
  if (!colors) return EMPTY_TERMINAL
  return {
    mode: modeFromBackground(colors.defaultBackground),
    background: colors.defaultBackground,
    foreground: colors.defaultForeground,
    colors: colors.palette,
  }
}

/**
 * Read the terminal palette and keep following its appearance. Called once at
 * startup with the renderer that is about to draw the app.
 */
export async function initTheme(
  renderer: CliRenderer,
  id?: string,
  mode?: ThemeMode,
): Promise<void> {
  if (id) setTheme(id)
  if (mode) setThemeMode(mode)

  await refreshTerminalPalette(renderer)
  renderer.on("theme_mode", () => {
    void refreshTerminalPalette(renderer)
  })
}

async function refreshTerminalPalette(renderer: CliRenderer): Promise<void> {
  try {
    const detected = await renderer.getPalette({ timeout: 400 })
    setTerminal(terminalFromColors(detected))
  } catch {
    // Terminals that do not answer OSC queries keep the fallback palette.
  }
}
