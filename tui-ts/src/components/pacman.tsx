import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js"
import type { ColorInput } from "@opentui/core"
import { colors, theme } from "../theme"
import { logoRows, type LogoRow } from "./logo"

/**
 * Pac-Man Arcade Terminal UI Suite - Constants, types, and terminal renderers.
 * Renders with clean Unicode/ASCII art and 24-bit ANSI / RGB colors.
 */

export type AgentPhase = "thinking" | "searching" | "reading" | "writing" | "bash"

export interface PhaseMeta {
  id: AgentPhase
  label: string
  short: string
  name: string
  ghostName: string
  color: string // Hex color
}

export const PACMAN_PALETTE = {
  pacman: "#FFD700",       // Gold Yellow
  inky: "#00E5FF",         // Cyan (thinking)
  clyde: "#FFB852",        // Orange (searching)
  pinky: "#FFB8FF",        // Pink (reading)
  blinky: "#FF3B30",       // Red (bash / tool execution)
  frightened: "#2121FF",   // Dark Blue
  frightenedWhite: "#FFFFFF",
  mazeBlue: "#1919A6",     // Neon Maze Blue
  successGreen: "#32D74B", // Emerald Green
  pellet: "#FFB897",       // Peach Pellet
  dimPellet: "#4C4C63",    // Dim Pellet
} as const

export const PHASE_CONFIG: Record<AgentPhase, PhaseMeta> = {
  thinking: {
    id: "thinking",
    label: "thinking (Inky)",
    short: "THINK",
    name: "thinking",
    ghostName: "Inky",
    color: PACMAN_PALETTE.inky,
  },
  searching: {
    id: "searching",
    label: "searching (Clyde)",
    short: "SEARCH",
    name: "searching the web",
    ghostName: "Clyde",
    color: PACMAN_PALETTE.clyde,
  },
  reading: {
    id: "reading",
    label: "reading (Pinky)",
    short: "READ",
    name: "reading pages",
    ghostName: "Pinky",
    color: PACMAN_PALETTE.pinky,
  },
  writing: {
    id: "writing",
    label: "writing (Pac-Man)",
    short: "WRITE",
    name: "streaming answer",
    ghostName: "Frightened Ghost",
    color: PACMAN_PALETTE.pacman,
  },
  bash: {
    id: "bash",
    label: "bash tool (Blinky)",
    short: "BASH",
    name: "tool execution",
    ghostName: "Blinky",
    color: PACMAN_PALETTE.blinky,
  },
}

/**
 * Animated Pac-Man mouth frames:
 * dir: 1 = right, -1 = left
 */
export function getPacManChar(dir: 1 | -1, tick: number): string {
  const mouthFrame = tick % 4
  if (dir === 1) {
    switch (mouthFrame) {
      case 0: return "ᗧ"
      case 1: return "C"
      case 2: return "O"
      case 3: return "C"
    }
  } else {
    switch (mouthFrame) {
      case 0: return "ᗤ"
      case 1: return "◖"
      case 2: return "O"
      case 3: return "◖"
    }
  }
  return "ᗧ"
}

/**
 * Ghost character: ᗣ
 */
export function getGhostChar(frightened: boolean, tick: number): string {
  if (frightened) {
    return tick % 4 < 2 ? "ᗣ" : "M"
  }
  return "ᗣ"
}

export function getGhostColor(phase: AgentPhase, tick: number): string {
  if (phase === "writing") {
    return tick % 6 < 3 ? PACMAN_PALETTE.frightened : PACMAN_PALETTE.frightenedWhite
  }
  return PHASE_CONFIG[phase].color
}

/** Shimmer text effect for phase status names */
export function getShimmerIndices(length: number, tick: number): { bright: boolean; veryBright: boolean }[] {
  const pos = ((tick * 0.7) % (length + 6)) - 3
  const out = []
  for (let i = 0; i < length; i++) {
    const dist = Math.abs(i - pos)
    out.push({
      bright: dist < 3,
      veryBright: dist < 2,
    })
  }
  return out
}

/**
 * 4 Synchronized Pac-Man Arcade TUI Components:
 *
 * 1. <ArcadeStageTrack /> - Horizontal neon-blue maze track across 4 stages (THINK, SEARCH/BASH, READ, WRITE)
 *    with a bonus cherry rack (🍒) showing completed stages, animated Pac-Man & Ghost, live step log feed (`› ...`)
 *    with blinking cursor (`▌`), and tok/s pellet-eater progress bar.
 *
 * 2. <PacManLogoWave /> - Sweep a golden Pac-Man & phase-colored wave across the GEMMA ASCII banner,
 *    and render a single-line arcade chase lane beneath where Pac-Man eats pellets, hits Power Pellet,
 *    and turns to chase ghosts.
 *
 * 3. <PacManCorridorBar /> - Compact status strip directly above composer input while busy with chomping Pac-Man,
 *    frightened/phase ghost, shimmer label, and elapsed timer.
 *
 * 4. <PacManHeaderSpinner /> - Inline indicator in assistant message header beside phase ghost, shimmer status,
 *    and elapsed time.
 */

// Global or shared tick hook for synchronization
export function usePacmanTick(intervalMs = 80) {
  const [tick, setTick] = createSignal(0)
  createEffect(() => {
    const timer = setInterval(() => setTick((t) => (t + 1) % 100000), intervalMs)
    onCleanup(() => clearInterval(timer))
  })
  return tick
}

/** Shimmer text helper component */
export function ShimmerText(props: { text: string; color: string; tick: number }) {
  const items = createMemo(() => {
    const shimmers = getShimmerIndices(props.text.length, props.tick)
    return props.text.split("").map((ch, i) => {
      const s = shimmers[i]
      return {
        ch,
        color: props.color,
        bold: s?.bright ?? false,
      }
    })
  })

  return (
    <text>
      <For each={items()}>
        {(item) => (
          <Show
            when={item.bold}
            fallback={<span style={{ fg: item.color }}>{item.ch}</span>}
          >
            <b style={{ fg: item.color }}>{item.ch}</b>
          </Show>
        )}
      </For>
    </text>
  )
}

/**
 * 1. <ArcadeStageTrack />
 * Replaces multi-box pipelines during active work.
 */
export function ArcadeStageTrack(props: {
  phase: AgentPhase
  stepLog?: string
  elapsed: number
  tokensPerSecond: number
  width: number
}) {
  const tick = usePacmanTick(90)

  const activeIdx = createMemo(() => {
    switch (props.phase) {
      case "thinking": return 0
      case "searching":
      case "bash": return 1
      case "reading": return 2
      case "writing": return 3
    }
  })

  const stages = createMemo(() => [
    { label: "THINK", id: "thinking" as const },
    { label: props.phase === "bash" ? "BASH" : "SEARCH", id: props.phase === "bash" ? ("bash" as const) : ("searching" as const) },
    { label: "READ", id: "reading" as const },
    { label: "WRITE", id: "writing" as const },
  ])

  const meta = createMemo(() => PHASE_CONFIG[props.phase])
  const frightened = createMemo(() => props.phase === "writing")

  // Bonus cherries rack
  const fruitRack = createMemo(() => {
    const act = activeIdx()
    const out: Array<{ char: string; color: ColorInput }> = []
    for (let i = 0; i < 4; i++) {
      if (i < act) {
        out.push({ char: "🍒", color: PACMAN_PALETTE.blinky })
      } else {
        out.push({ char: "·", color: PACMAN_PALETTE.dimPellet })
      }
    }
    return out
  })

  // Horizontal maze track cells calculation
  const trackCells = createMemo(() => {
    const totalW = Math.max(30, Math.min(props.width - 4, 88))
    const act = activeIdx()
    // 4 checkpoint anchors distributed evenly
    const step = Math.floor((totalW - 6) / 3)
    const anchors = [2, 2 + step, 2 + step * 2, totalW - 4]

    // Pac-Man & Ghost positions centered on active checkpoint corridor
    const currentAnchor = anchors[act] ?? 2
    const pacPos = Math.max(0, currentAnchor - 1 + (tick() % 2 === 0 ? 0 : 1))
    const ghostPos = Math.max(0, currentAnchor + 2)

    const cells: Array<{ ch: string; color: ColorInput; bold?: boolean }> = []
    for (let x = 0; x < totalW; x++) {
      const anchorIdx = anchors.indexOf(x)
      if (x === pacPos) {
        cells.push({
          ch: getPacManChar(1, tick()),
          color: PACMAN_PALETTE.pacman,
          bold: true,
        })
      } else if (x === ghostPos) {
        cells.push({
          ch: getGhostChar(frightened(), tick()),
          color: getGhostColor(props.phase, tick()),
          bold: true,
        })
      } else if (anchorIdx !== -1) {
        if (anchorIdx < act) {
          cells.push({ ch: "✓", color: PACMAN_PALETTE.successGreen, bold: true })
        } else if (anchorIdx === act) {
          cells.push({ ch: "◉", color: meta().color, bold: true })
        } else {
          cells.push({ ch: "◉", color: PACMAN_PALETTE.pellet })
        }
      } else {
        // Between checkpoints: dots or corridor lane
        const inCompletedSection = x < anchors[act]!
        if (inCompletedSection) {
          cells.push({ ch: " ", color: "" }) // Eaten pellets
        } else if (x % 2 === 0) {
          cells.push({ ch: "·", color: PACMAN_PALETTE.pellet })
        } else {
          cells.push({ ch: " ", color: "" })
        }
      }
    }
    return { totalW, cells, anchors }
  })

  // Tok/s pellet-eater progress bar
  const tokBar = createMemo(() => {
    const barWidth = Math.max(16, Math.min(props.width - 24, 46))
    const tps = Math.round(props.tokensPerSecond)
    // Advance pac-man based on tok/s and tick
    const speed = props.phase === "writing" ? Math.max(1, Math.min(barWidth - 2, Math.floor((tick() * 1.5) % barWidth))) : 1
    const cells: Array<{ ch: string; color: ColorInput; bold?: boolean }> = []
    for (let i = 0; i < barWidth; i++) {
      if (i === speed) {
        cells.push({
          ch: getPacManChar(1, tick()),
          color: PACMAN_PALETTE.pacman,
          bold: true,
        })
      } else if (i < speed) {
        cells.push({ ch: " ", color: "" })
      } else if (i % 2 === 0) {
        cells.push({ ch: "·", color: PACMAN_PALETTE.pellet })
      } else {
        cells.push({ ch: " ", color: "" })
      }
    }
    return { barWidth, tps, cells }
  })

  const borderLine = createMemo(() => {
    const w = trackCells().totalW
    return "═".repeat(w)
  })

  return (
    <box
      flexDirection="column"
      border
      borderStyle="rounded"
      borderColor={colors().border}
      backgroundColor={colors().card}
      paddingLeft={1}
      paddingRight={1}
      marginTop={1}
      marginBottom={1}
      flexShrink={0}
      width={Math.min(props.width, trackCells().totalW + 4)}
    >
      {/* Track Header: Phase Status & Fruit Rack */}
      <box flexDirection="row" justifyContent="space-between" height={1} flexShrink={0}>
        <box flexDirection="row" gap={1}>
          <ShimmerText text={meta().name} color={meta().color} tick={tick()} />
          <text style={{ fg: colors().muted }}>· {props.elapsed.toFixed(1)}s</text>
        </box>
        <box flexDirection="row" gap={1}>
          <text style={{ fg: colors().faint }}>BONUS</text>
          <For each={fruitRack()}>
            {(fruit) => <text style={{ fg: fruit.color }}>{fruit.char}</text>}
          </For>
        </box>
      </box>

      {/* Horizontal Track Border */}
      <box flexDirection="column" marginTop={0} marginBottom={0} flexShrink={0}>
        <text style={{ fg: colors().borderSoft }}>{borderLine()}</text>
        <text>
          <For each={trackCells().cells}>
            {(cell) => (
              <Show when={cell.bold} fallback={<span style={cell.color ? { fg: cell.color } : {}}>{cell.ch}</span>}>
                <b style={cell.color ? { fg: cell.color } : {}}>{cell.ch}</b>
              </Show>
            )}
          </For>
        </text>
        <text style={{ fg: colors().borderSoft }}>{borderLine()}</text>
      </box>

      {/* Stage Labels */}
      <box flexDirection="row" justifyContent="space-between" height={1} flexShrink={0}>
        <For each={stages()}>
          {(st, idx) => {
            const isDone = idx() < activeIdx()
            const isActive = idx() === activeIdx()
            const labelCol = isDone
              ? PACMAN_PALETTE.successGreen
              : isActive
                ? meta().color
                : colors().faint
            return (
              <text style={{ fg: labelCol }}>
                <Show when={isActive} fallback={isDone ? `✓ ${st.label}` : st.label}>
                  <b>{isDone ? `✓ ${st.label}` : st.label}</b>
                </Show>
              </text>
            )
          }}
        </For>
      </box>

      {/* Step Log Feed with Blinking Cursor */}
      <Show when={props.stepLog}>
        <box flexDirection="row" height={1} marginTop={1} flexShrink={0}>
          <text style={{ fg: meta().color }} marginRight={1}>
            <b>›</b>
          </text>
          <text style={{ fg: colors().text }} truncate flexGrow={1}>
            {props.stepLog}
          </text>
          <Show when={tick() % 6 < 3}>
            <text style={{ fg: meta().color }}>▌</text>
          </Show>
        </box>
      </Show>

      {/* tok/s Pellet-Eater Bar */}
      <box flexDirection="row" height={1} marginTop={1} alignItems="center" gap={1} flexShrink={0}>
        <text style={{ fg: colors().faint }}>TOK/S</text>
        <text>
          <For each={tokBar().cells}>
            {(c) => (
              <Show when={c.bold} fallback={<span style={c.color ? { fg: c.color } : {}}>{c.ch}</span>}>
                <b style={c.color ? { fg: c.color } : {}}>{c.ch}</b>
              </Show>
            )}
          </For>
        </text>
        <text style={{ fg: PACMAN_PALETTE.pacman }}>
          <b>{`${tokBar().tps} t/s`}</b>
        </text>
      </box>
    </box>
  )
}

/**
 * 2. <PacManLogoWave />
 * Landing Logo Wave across GEMMA ASCII banner & 4-Ghost Arcade Chase Lane beneath.
 */
export function PacManLogoWave(props: {
  rows: string[]
  phase?: AgentPhase
  tick?: number
}) {
  const internalTick = usePacmanTick(80)
  const tick = () => (props.tick !== undefined ? props.tick : internalTick())
  const activePhase = () => props.phase ?? "thinking"
  const meta = () => PHASE_CONFIG[activePhase()]

  // The rich theme colour gradient (accent -> info -> danger) with sweeping Pac-Man wave
  const bannerRows = createMemo(() => {
    const waveX = Math.floor((tick() * 1.5) % 64) - 8
    const baseGradient = logoRows(props.rows, {
      accent: theme().accent,
      info: colors().info,
      danger: colors().danger,
      page: colors().page,
      light: theme().mode === "light",
      sweep: null,
      solid: props.rows.length <= 3,
    })

    return baseGradient.map((r) =>
      r.cells.map((cell, x) => {
        if (!cell.ch.trim()) {
          return { ch: cell.ch, color: "" as ColorInput, bold: false }
        }
        const d = Math.abs(x - waveX)
        const isPacCrest = d < 4
        const col: ColorInput = isPacCrest ? PACMAN_PALETTE.pacman : cell.color
        return {
          ch: cell.ch,
          color: col,
          bold: isPacCrest || cell.ch === "█",
        }
      }),
    )
  })

  // Single-line Arcade Chase Lane beneath banner
  const chaseLane = createMemo(() => {
    const width = 47 // Matching standard GEMMA banner width
    const cycle = tick() % 140
    const powered = cycle > 70
    const dir: 1 | -1 = powered ? -1 : 1
    const trackLen = width - 6
    const pacX = powered
      ? Math.floor((width - 3) - ((cycle - 70) / 70) * trackLen)
      : Math.floor(2 + (cycle / 70) * trackLen)

    const cells: Array<{ ch: string; color: ColorInput; bold?: boolean }> = []
    const ghostColors = [
      PACMAN_PALETTE.blinky,
      PACMAN_PALETTE.pinky,
      PACMAN_PALETTE.inky,
      PACMAN_PALETTE.clyde,
    ]

    for (let x = 0; x < width; x++) {
      if (x === pacX) {
        cells.push({
          ch: getPacManChar(dir, tick()),
          color: PACMAN_PALETTE.pacman,
          bold: true,
        })
        continue
      }

      // Check ghosts
      let ghostHit = false
      for (let g = 0; g < ghostColors.length; g++) {
        const gx = pacX - dir * (4 + g * 3)
        if (x === gx) {
          const gCol = powered
            ? (tick() % 6 < 3 ? PACMAN_PALETTE.frightened : PACMAN_PALETTE.frightenedWhite)
            : ghostColors[g]!
          cells.push({
            ch: getGhostChar(powered, tick() + g),
            color: gCol,
            bold: true,
          })
          ghostHit = true
          break
        }
      }
      if (ghostHit) continue

      // Power pellet or standard pellets
      if (!powered && x === width - 3) {
        const powerPulse = tick() % 4 < 2
        cells.push({
          ch: "◉",
          color: PACMAN_PALETTE.pacman,
          bold: powerPulse,
        })
      } else if (!powered && x > pacX + 2 && x % 2 === 0) {
        cells.push({ ch: "·", color: PACMAN_PALETTE.pellet })
      } else {
        cells.push({ ch: " ", color: "" })
      }
    }

    return cells
  })

  return (
    <box flexDirection="column" alignItems="center" flexShrink={0}>
      <For each={bannerRows()}>
        {(row) => (
          <text flexShrink={0}>
            <For each={row}>
              {(cell) => (
                <Show when={cell.bold} fallback={<span style={cell.color ? { fg: cell.color } : {}}>{cell.ch}</span>}>
                  <b style={cell.color ? { fg: cell.color } : {}}>{cell.ch}</b>
                </Show>
              )}
            </For>
          </text>
        )}
      </For>

      {/* Single-line Chase Lane */}
      <box flexDirection="row" marginTop={1} height={1} flexShrink={0}>
        <text>
          <For each={chaseLane()}>
            {(c) => (
              <Show when={c.bold} fallback={<span style={c.color ? { fg: c.color } : {}}>{c.ch}</span>}>
                <b style={c.color ? { fg: c.color } : {}}>{c.ch}</b>
              </Show>
            )}
          </For>
        </text>
      </box>
    </box>
  )
}

/**
 * 3. <PacManCorridorBar />
 * Compact, clean status strip directly above the composer input while busy.
 * Styled with subtle native theme borders (no blue maze box) and minimal stage breadcrumbs.
 */
export function PacManCorridorBar(props: {
  phase: AgentPhase
  elapsed: number
  width: number
  tokensPerSecond?: number
}) {
  const tick = usePacmanTick(80)
  const meta = createMemo(() => PHASE_CONFIG[props.phase])
  const frightened = createMemo(() => props.phase === "writing")

  const activeIdx = createMemo(() => {
    switch (props.phase) {
      case "thinking": return 0
      case "searching":
      case "bash": return 1
      case "reading": return 2
      case "writing": return 3
    }
  })

  // 4 stages definition
  const stages = [
    { label: "THINK", id: "thinking" as const },
    { label: props.phase === "bash" ? "BASH" : "SEARCH", id: props.phase === "bash" ? ("bash" as const) : ("searching" as const) },
    { label: "READ", id: "reading" as const },
    { label: "WRITE", id: "writing" as const },
  ]

  // Clean, modern single-line status row
  return (
    <box
      flexDirection="row"
      width="100%"
      height={1}
      alignItems="center"
      justifyContent="space-between"
      flexShrink={0}
    >
      {/* Left: Phase indicator with animated Pac-Man / Ghost & elapsed timer */}
      <box flexDirection="row" alignItems="center" gap={1} flexShrink={0}>
        <text style={{ fg: PACMAN_PALETTE.pacman }}>
          <b>{getPacManChar(1, tick())}</b>
        </text>
        <text style={{ fg: getGhostColor(props.phase, tick()) }}>
          <b>{getGhostChar(frightened(), tick())}</b>
        </text>
        <ShimmerText text={meta().name} color={meta().color} tick={tick()} />
        <text style={{ fg: colors().muted }}>· {props.elapsed.toFixed(1)}s</text>
        <Show when={props.tokensPerSecond && props.tokensPerSecond > 0 && props.phase === "writing"}>
          <text style={{ fg: colors().faint }}>· {Math.round(props.tokensPerSecond!)} tok/s</text>
        </Show>
      </box>

      {/* Right: Minimal inline stage breadcrumb: THINK › SEARCH › READ › WRITE */}
      <box flexDirection="row" alignItems="center" gap={1} flexShrink={0}>
        <For each={stages}>
          {(st, idx) => {
            const isDone = idx() < activeIdx()
            const isActive = idx() === activeIdx()
            const labelCol = isDone
              ? PACMAN_PALETTE.successGreen
              : isActive
                ? meta().color
                : colors().faint
            const icon = isDone ? "✓" : isActive ? "●" : "○"
            return (
              <box flexDirection="row" alignItems="center" gap={1}>
                <Show when={idx() > 0}>
                  <text style={{ fg: colors().faint }}>›</text>
                </Show>
                <text style={{ fg: labelCol }}>
                  <Show when={isActive} fallback={`${icon} ${st.label}`}>
                    <b>{`${icon} ${st.label}`}</b>
                  </Show>
                </text>
              </box>
            )
          }}
        </For>
      </box>
    </box>
  )
}

/**
 * 4. <PacManHeaderSpinner />
 * Compact inline indicator in the assistant message header.
 */
export function PacManHeaderSpinner(props: {
  phase: AgentPhase
  elapsed: number
}) {
  const tick = usePacmanTick(90)
  const meta = createMemo(() => PHASE_CONFIG[props.phase])
  const frightened = createMemo(() => props.phase === "writing")

  const dots = createMemo(() => {
    const shift = tick() % 3
    const cells: string[] = []
    for (let i = 0; i < 3; i++) {
      cells.push((i + shift) % 2 === 0 ? "·" : " ")
    }
    return cells.join("")
  })

  return (
    <box flexDirection="row" height={1} gap={1} alignItems="center" flexShrink={0}>
      <text style={{ fg: PACMAN_PALETTE.pacman }}>
        <b>{getPacManChar(1, tick())}</b>
      </text>
      <text style={{ fg: PACMAN_PALETTE.pellet }}>{dots()}</text>
      <text style={{ fg: getGhostColor(props.phase, tick()) }}>
        <b>{getGhostChar(frightened(), tick())}</b>
      </text>
      <ShimmerText text={meta().name} color={meta().color} tick={tick()} />
      <text style={{ fg: colors().muted }}>· {props.elapsed.toFixed(1)}s</text>
    </box>
  )
}
