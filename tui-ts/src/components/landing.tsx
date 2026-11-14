import { For, Show, createMemo } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import type { ColorInput } from "@opentui/core"
import { colors } from "../theme"

/**
 * Exact 8-row bitmap font for the wordmark. `#` is a pixel, `.` is empty;
 * letters are joined with a single empty column (26 columns in total).
 */
const FONT: Record<string, string[]> = {
  g: ["####", "#..#", "#..#", "####", "...#", "...#", "####", "...."],
  e: ["####", "#..#", "####", "#...", "####", "....", "....", "...."],
  m: ["#####", "#.#.#", "#.#.#", "#.#.#", "#.#.#", ".....", ".....", "....."],
  a: ["####", "...#", "####", "#..#", "####", "....", "....", "...."],
}

/** `gem` is the dim half of the wordmark, `ma` the bright half. */
const WORD = ["g", "e", "m", "m", "a"]
const LETTER_COLS = 3

/** Empty pixels enclosed by a letter (the counters) are inked, not canvas. */
const HOLES: Record<string, [number, number][]> = {
  g: [
    [1, 1],
    [1, 2],
    [2, 1],
    [2, 2],
  ],
  e: [
    [1, 1],
    [1, 2],
  ],
  a: [
    [3, 1],
    [3, 2],
  ],
}

type Tone = "letter-muted" | "letter-bright" | "hole" | "bg"

const letterTone = (index: number): Tone =>
  index < LETTER_COLS ? "letter-muted" : "letter-bright"

/** Theme token for one pixel of the flattened wordmark (spacers stay canvas). */
function pixelTone(row: number, col: number): Tone {
  let offset = 0
  for (let index = 0; index < WORD.length; index += 1) {
    const letter = WORD[index]!
    const glyph = FONT[letter]!
    const width = glyph[0]!.length
    if (col < offset + width) {
      const inner = col - offset
      if (glyph[row]![inner] === "#") return letterTone(index)
      const hole = HOLES[letter]?.some(([holeRow, holeCol]) => holeRow === row && holeCol === inner)
      return hole ? "hole" : "bg"
    }
    offset += width + 1
  }
  return "bg"
}

function toneColor(tone: Tone): ColorInput {
  const palette = colors()
  switch (tone) {
    case "letter-muted":
      return palette.dim
    case "letter-bright":
      return palette.text
    case "hole":
      return palette.panel
    default:
      return palette.page
  }
}

interface Run {
  top: Tone
  bottom: Tone
  text: string
}

/**
 * The wordmark drawn with half blocks: 8 pixel rows become 4 terminal rows,
 * each cell `▀` with the top pixel as foreground and the bottom as background.
 * Runs are grouped by theme token, so the colors stay live.
 */
const LOGO_ROWS: Run[][] = [0, 1, 2, 3].map((outRow) => {
  const width = WORD.reduce((sum, letter) => sum + FONT[letter]![0]!.length, 0) + WORD.length - 1
  const runs: Run[] = []
  let current: Run | null = null
  for (let col = 0; col < width; col += 1) {
    const top = pixelTone(outRow * 2, col)
    const bottom = pixelTone(outRow * 2 + 1, col)
    if (current && current.top === top && current.bottom === bottom) {
      current.text += "▀"
    } else {
      current = { top, bottom, text: "▀" }
      runs.push(current)
    }
  }
  return runs
})

function LogoRow(props: { row: number }) {
  const runs = createMemo(() =>
    LOGO_ROWS[props.row]!.map((run) => ({
      fg: toneColor(run.top),
      bg: toneColor(run.bottom),
      text: run.text,
    })),
  )
  return (
    <text flexShrink={0}>
      <For each={runs()}>
        {(run) => <span style={{ fg: run.fg, bg: run.bg }}>{run.text}</span>}
      </For>
    </text>
  )
}

/**
 * Gemini-style home canvas: the centred bitmap wordmark, the greeting and the
 * prompt pill. Purely presentational — the prompt is passed in as `children`.
 * Every color comes from the active theme, same as the chat view.
 */
export function Landing(props: { children: unknown }) {
  const dims = useTerminalDimensions()
  const narrow = () => dims().width < 40

  return (
    <box flexGrow={1} minHeight={0} flexDirection="column" backgroundColor={colors().page}>
      <box
        flexGrow={1}
        minHeight={0}
        flexDirection="column"
        alignItems="center"
        justifyContent="center"
        paddingLeft={2}
        paddingRight={2}
      >
        <Show
          when={!narrow()}
          fallback={
            <text>
              <b style={{ fg: colors().dim }}>gem</b>
              <b style={{ fg: colors().text }}>ma</b>
            </text>
          }
        >
          <box flexDirection="column" alignItems="center" flexShrink={0}>
            <For each={[0, 1, 2, 3]}>{(row) => <LogoRow row={row} />}</For>
          </box>
        </Show>

        <text marginTop={2} flexShrink={0}>
          <b style={{ fg: colors().text }}>Where should we start?</b>
        </text>

        <box paddingTop={2} alignItems="center" flexShrink={0}>
          {props.children as never}
        </box>
      </box>
    </box>
  )
}
