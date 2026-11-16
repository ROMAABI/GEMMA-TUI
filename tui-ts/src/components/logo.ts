/**
 * The landing wordmark: static ASCII art plus the colour rules that turn it
 * into a gradient logo. The art is generated with figlet ("ANSI Shadow") and
 * pasted here, so nothing depends on figlet at runtime.
 */

import { parseColor, type ColorInput } from "@opentui/core"
import { tint } from "../theme"

/** figlet -f "ANSI Shadow" gemma */
export const LOGO_ROWS: string[] = [
  " ██████╗ ███████╗███╗   ███╗███╗   ███╗ █████╗ ",
  "██╔════╝ ██╔════╝████╗ ████║████╗ ████║██╔══██╗",
  "██║  ███╗█████╗  ██╔████╔██║██╔████╔██║███████║",
  "██║   ██║██╔══╝  ██║╚██╔╝██║██║╚██╔╝██║██╔══██║",
  "╚██████╔╝███████╗██║ ╚═╝ ██║██║ ╚═╝ ██║██║  ██║",
  " ╚═════╝ ╚══════╝╚═╝     ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝",
]

export const LOGO_WIDTH = 47

/** Narrow-terminal fallback: the same word in three rows of box drawing. */
export const COMPACT_ROWS: string[] = [
  "╔═╗╔═╗╔╦╗╔╦╗╔═╗",
  "║ ╦║╣ ║║║║║║╠═╣",
  "╚═╝╚═╝╩ ╩╩ ╩╩ ╩",
]

export const COMPACT_WIDTH = 26

export const TAGLINE = "local coding agent · offline"

/** One-time shimmer: a highlight sweeping left to right. */
export const SHIMMER_MS = 800
export const SHIMMER_STEP = 30
export const SHIMMER_REACH = 5

/** True when the terminal can show 24-bit colours (the shimmer needs them). */
export function supportsTruecolor(): boolean {
  const colorterm = (process.env.COLORTERM ?? "").toLowerCase()
  if (colorterm.includes("truecolor") || colorterm.includes("24bit")) return true
  const term = (process.env.TERM ?? "").toLowerCase()
  return term.includes("256color") || term.includes("direct") || term.includes("truecolor")
}

interface Hsl {
  h: number
  s: number
  l: number
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function toHex(color: ColorInput): string {
  const rgba = parseColor(color)
  const part = (value: number) =>
    clamp(Math.round(value * 255), 0, 255).toString(16).padStart(2, "0")
  return `#${part(rgba.r)}${part(rgba.g)}${part(rgba.b)}`
}

/** RGBA channels are 0..1 floats. */
function toRgb(color: ColorInput): [number, number, number] {
  const rgba = parseColor(color)
  return [rgba.r, rgba.g, rgba.b]
}

function rgbToHsl(rgb: [number, number, number]): Hsl {
  const r = rgb[0]
  const g = rgb[1]
  const b = rgb[2]
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return { h: 0, s: 0, l }
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
  else if (max === g) h = (b - r) / d + 2
  else h = (r - g) / d + 4
  return { h: h * 60, s, l }
}

function hslToRgb(hsl: Hsl): [number, number, number] {
  const h = (((hsl.h % 360) + 360) % 360) / 60
  const c = (1 - Math.abs(2 * hsl.l - 1)) * hsl.s
  const x = c * (1 - Math.abs((h % 2) - 1))
  let rgb: [number, number, number]
  if (h < 1) rgb = [c, x, 0]
  else if (h < 2) rgb = [x, c, 0]
  else if (h < 3) rgb = [0, c, x]
  else if (h < 4) rgb = [0, x, c]
  else if (h < 5) rgb = [x, 0, c]
  else rgb = [c, 0, x]
  const m = hsl.l - c / 2
  return [rgb[0] + m, rgb[1] + m, rgb[2] + m]
}

function rgbToHex(rgb: [number, number, number]): string {
  const part = (value: number) =>
    clamp(Math.round(value * 255), 0, 255).toString(16).padStart(2, "0")
  return `#${part(rgb[0])}${part(rgb[1])}${part(rgb[2])}`
}

/**
 * Hue step between two anchors. The short way is used unless it crosses the
 * green/yellow band, which would read as muddy next to a blue accent.
 */
function hueArc(from: number, to: number): number {
  let delta = (((to - from) % 360) + 540) % 360 - 180
  const crossesGreen = (start: number, step: number) => {
    for (let i = 1; i < 72; i += 1) {
      const hue = (((start + (step * i) / 72) % 360) + 360) % 360
      if (hue > 70 && hue < 165) return true
    }
    return false
  }
  if (crossesGreen(from, delta)) delta = delta >= 0 ? delta - 360 : delta + 360
  return delta
}

export interface LogoCell {
  ch: string
  color: string
}

export interface LogoRow {
  cells: LogoCell[]
}

export interface LogoTheme {
  /** Theme accent — the left end of the gradient. */
  accent: ColorInput
  /** Blue-ish secondary in the middle. */
  info: ColorInput
  /** Warm secondary on the right. */
  danger: ColorInput
  /** Page background, used to darken the extruded edges. */
  page: ColorInput
  /** Light themes need darker faces to keep the contrast. */
  light?: boolean
  /** Current shimmer position, or null when the logo is static. */
  sweep: number | null
  /** True when every glyph is a face glyph (the compact fallback). */
  solid?: boolean
}

/**
 * Build the coloured rows. The face glyphs carry a horizontal gradient from
 * the theme accent through the secondary colours; the extruded edges use the
 * same hue darkened towards the page background.
 */
export function logoRows(rows: string[], theme: LogoTheme): LogoRow[] {
  const width = Math.max(...rows.map((row) => row.length))
  const page = toHex(theme.page)
  const anchors = [theme.accent, theme.info, theme.danger].map((color) => rgbToHsl(toRgb(color)))
  const arcs = [
    hueArc(anchors[0]!.h, anchors[1]!.h),
    hueArc(anchors[1]!.h, anchors[2]!.h),
  ]

  const gradientAt = (x: number) => {
    const t = width > 1 ? x / (width - 1) : 0
    const segment = t < 0.5 ? 0 : 1
    const local = segment === 0 ? t * 2 : (t - 0.5) * 2
    const from = anchors[segment]!
    const to = anchors[segment + 1]!
    return {
      h: from.h + arcs[segment]! * local,
      s: from.s + (to.s - from.s) * local,
      l: from.l + (to.l - from.l) * local,
    }
  }

  return rows.map((row, y) => {
    const cells: LogoCell[] = []
    for (let x = 0; x < row.length; x += 1) {
      const ch = row[x]!
      if (ch === " ") {
        cells.push({ ch, color: "" })
        continue
      }
      const base = gradientAt(x)
      const isFace = theme.solid === true || ch === "█"
      // A steady face lightness keeps the gradient vivid; the top row catches
      // a little extra light and the rows below fall away.
      const band = theme.light ? 0.46 : 0.64
      let lightness = clamp(band - y * 0.022, 0.08, 0.9)
      if (theme.sweep !== null) {
        const distance = Math.abs(x - theme.sweep)
        if (distance < SHIMMER_REACH) {
          lightness = clamp(
            lightness + (SHIMMER_REACH - distance) * (isFace ? 0.05 : 0.03),
            0.08,
            0.96,
          )
        }
      }
      const face = rgbToHex(
        hslToRgb({ h: base.h, s: clamp(Math.max(base.s, 0.55), 0, 1), l: lightness }),
      )
      cells.push({
        ch,
        color: isFace ? face : tint(face, page, 0.5),
      })
    }
    return { cells }
  })
}
