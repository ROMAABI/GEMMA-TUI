import { For, Show, createMemo, createSignal, onCleanup, onMount } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import { colors } from "../theme"
import {
  COMPACT_ROWS,
  LOGO_ROWS,
  LOGO_WIDTH,
  TAGLINE,
} from "./logo"
import { PacManLogoWave } from "./pacman"

/**
 * Gemini-style home canvas: the colourful wordmark with Pac-Man wave,
 * single-line 4-ghost arcade chase lane, tagline, greeting, and the prompt pill.
 */
export function Landing(props: { children: unknown }) {
  const dims = useTerminalDimensions()

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
          <PacManLogoWave
            rows={size() === "full" ? LOGO_ROWS : COMPACT_ROWS}
            phase="thinking"
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
