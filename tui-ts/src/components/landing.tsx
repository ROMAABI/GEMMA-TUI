import { For, Show, createMemo, createSignal, onCleanup, onMount } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import { colors, theme } from "../theme"
import {
  COMPACT_ROWS,
  LOGO_ROWS,
  LOGO_WIDTH,
  SHIMMER_MS,
  SHIMMER_STEP,
  TAGLINE,
  logoRows,
  supportsTruecolor,
  type LogoRow,
} from "./logo"

/** The coloured wordmark: gradient faces, darkened extrusion, one shimmer. */
function Wordmark(props: { rows: string[]; sweep: number | null; solid?: boolean }) {
  const rendered = createMemo<LogoRow[]>(() =>
    logoRows(props.rows, {
      accent: theme().accent,
      info: colors().info,
      danger: colors().danger,
      page: colors().page,
      light: theme().mode === "light",
      sweep: props.sweep,
      solid: props.solid,
    }),
  )
  return (
    <box flexDirection="column" alignItems="center" flexShrink={0}>
      <For each={rendered()}>
        {(row) => (
          <text flexShrink={0}>
            <For each={row.cells}>
              {(cell) => (
                <span style={cell.color ? { fg: cell.color } : {}}>{cell.ch}</span>
              )}
            </For>
          </text>
        )}
      </For>
    </box>
  )
}

/**
 * Gemini-style home canvas: the colourful wordmark, the tagline, the greeting
 * and the prompt pill. Purely presentational — the prompt is passed in as
 * `children`. Every colour comes from the active theme.
 */
export function Landing(props: { children: unknown }) {
  const dims = useTerminalDimensions()
  const [sweep, setSweep] = createSignal<number | null>(null)
  let timer: ReturnType<typeof setInterval> | null = null

  const runShimmer = () => {
    // No truecolor, no shimmer — the gradient stays, the sweep is skipped.
    if (!supportsTruecolor()) return
    if (timer) clearInterval(timer)
    const steps = Math.max(1, Math.round(SHIMMER_MS / SHIMMER_STEP))
    const step = (LOGO_WIDTH + 12) / steps
    let position = -6
    setSweep(position)
    timer = setInterval(() => {
      position += step
      if (position > LOGO_WIDTH + 6) {
        if (timer) clearInterval(timer)
        timer = null
        setSweep(null)
        return
      }
      setSweep(position)
    }, SHIMMER_STEP)
  }

  onMount(runShimmer)
  onCleanup(() => {
    if (timer) clearInterval(timer)
  })

  const size = createMemo<"full" | "compact" | "plain">(() => {
    const width = dims().width
    if (width < 30) return "plain"
    if (width < LOGO_WIDTH + 4) return "compact"
    return "full"
  })

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
        <Show when={size() !== "plain"} fallback={<Text>GEMMA</Text>}>
          <Wordmark
            rows={size() === "full" ? LOGO_ROWS : COMPACT_ROWS}
            sweep={sweep()}
            solid={size() === "compact"}
          />
        </Show>

        <text marginTop={1} flexShrink={0} style={{ fg: colors().dim }}>
          {TAGLINE}
        </text>

        <text marginTop={1} flexShrink={0}>
          <b style={{ fg: colors().text }}>Where should we start?</b>
        </text>

        <box paddingTop={1} alignItems="center" flexShrink={0}>
          {props.children as never}
        </box>
      </box>
    </box>
  )
}

/** Tiny helper so the plain fallback keeps the same markup shape. */
function Text(props: { children: string }) {
  return (
    <text>
      <b style={{ fg: colors().text }}>{props.children}</b>
    </text>
  )
}
