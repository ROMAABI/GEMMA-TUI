import { For, Show, createEffect, createMemo, createSignal, on } from "solid-js"
import type { KeyEvent, TextareaRenderable } from "@opentui/core"
import type { CommandDef } from "../api"
import { colors, type ResolvedTheme } from "../theme"
import { truncate } from "../format"

const FALLBACK_COMMANDS: CommandDef[] = [
  { name: "/new", description: "Start a new session" },
  { name: "/clear", description: "Clear this session" },
  { name: "/model", description: "Pick a provider model" },
  { name: "/mode", description: "Switch chat / agent mode" },
  { name: "/theme", description: "Pick a theme or light/dark mode" },
  { name: "/rename", description: "Rename this session" },
  { name: "/delete", description: "Delete this session" },
  { name: "/help", description: "Show commands and keys" },
]

export interface Inject {
  id: number
  text: string
}

/** The Gemini-style prompt: one rounded pill with a status row underneath. */
export function Composer(props: {
  accent: ResolvedTheme
  commands: CommandDef[]
  streaming: boolean
  mode: "chat" | "agent"
  model: string
  provider: string
  active: boolean
  /** Width of the pill itself (computed by the app from the main area). */
  width: number
  placement: "above" | "below"
  focusSignal: number
  inject: Inject | null
  onSend: (text: string) => void
  onStop: () => void
  onCommand: (name: string) => void
  onToggleMode: () => void
  onModel: () => void
}) {
  const [value, setValue] = createSignal("")
  const [selected, setSelected] = createSignal(0)
  const [dismissed, setDismissed] = createSignal(false)
  let area: TextareaRenderable | undefined

  const commands = createMemo(() => (props.commands.length ? props.commands : FALLBACK_COMMANDS))

  const matches = createMemo(() => {
    const text = value()
    if (!text.startsWith("/") || text.includes(" ") || text.includes("\n")) return []
    return commands().filter((command) => command.name.startsWith(text.toLowerCase()))
  })

  const paletteOpen = createMemo(() => matches().length > 0 && !dismissed())

  createEffect(
    on(
      () => props.active,
      (active) => {
        if (active) area?.focus()
        else area?.blur()
      },
    ),
  )

  createEffect(
    on(
      () => props.focusSignal,
      () => {
        if (props.active) area?.focus()
      },
    ),
  )

  const setText = (text: string) => {
    if (!area) return
    area.editBuffer.setText(text)
    area.cursorOffset = text.length
    setValue(text)
    setDismissed(false)
    setSelected(0)
  }

  createEffect(
    on(
      () => props.inject?.id,
      () => {
        const injected = props.inject
        if (injected) setText(injected.text)
      },
    ),
  )

  const complete = () => {
    const match = matches()[Math.min(selected(), matches().length - 1)]
    if (match) setText(`${match.name} `)
  }

  const submit = () => {
    const text = area?.plainText ?? value()
    if (!text.trim()) return
    if (paletteOpen()) {
      const exact = commands().find((command) => command.name === text.trim())
      if (!exact) {
        complete()
        return
      }
    }
    setText("")
    setDismissed(false)
    props.onSend(text)
  }

  const onKeyDown = (event: KeyEvent) => {
    if (!paletteOpen()) {
      // Tab toggles chat/agent mode when the slash palette is closed.
      if (event.name === "tab") {
        event.preventDefault()
        props.onToggleMode()
      }
      return
    }
    if (event.name === "up" || (event.ctrl && event.name === "p")) {
      event.preventDefault()
      setSelected((prev) => (prev - 1 + matches().length) % matches().length)
    } else if (event.name === "down" || (event.ctrl && event.name === "n")) {
      event.preventDefault()
      setSelected((prev) => (prev + 1) % matches().length)
    } else if (event.name === "tab") {
      event.preventDefault()
      complete()
    } else if (event.name === "escape") {
      event.preventDefault()
      setDismissed(true)
    }
  }

  const handleContentChange = (_event: unknown) => {
    setValue(area?.plainText ?? "")
    setSelected(0)
    setDismissed(false)
  }

  const modelBudget = () => Math.max(6, props.width - 14)

  /** The palette shows a sliding window so long server command lists stay on screen. */
  const PALETTE_ROWS = 10
  const paletteWindow = createMemo(() => {
    const count = matches().length
    const selected0 = Math.min(Math.max(selected(), 0), Math.max(0, count - 1))
    const start = Math.max(0, Math.min(selected0 - (PALETTE_ROWS - 1), count - PALETTE_ROWS))
    return {
      start,
      items: matches().slice(start, start + PALETTE_ROWS).map((command, offset) => ({
        command,
        index: start + offset,
      })),
      hidden: Math.max(0, count - PALETTE_ROWS),
    }
  })

  const Palette = () => (
    <box
      flexDirection="column"
      border
      borderStyle="rounded"
      borderColor={colors().border}
      backgroundColor={colors().panel}
      paddingLeft={1}
      paddingRight={1}
      marginBottom={props.placement === "above" ? 1 : 0}
      marginTop={props.placement === "below" ? 1 : 0}
      flexShrink={0}
    >
      <For each={paletteWindow().items}>
        {(entry) => (
          <box
            flexDirection="row"
            gap={1}
            height={1}
            backgroundColor={entry.index === selected() ? colors().element : colors().panel}
            onMouseDown={() => {
              setSelected(entry.index)
              setText(`${entry.command.name} `)
            }}
          >
            <text style={{ fg: entry.index === selected() ? props.accent.accent : colors().faint }}>
              {entry.index === selected() ? "▸" : " "}
            </text>
            <text style={{ fg: entry.index === selected() ? colors().text : colors().dim }}>
              <b>{entry.command.name}</b>
            </text>
            <text style={{ fg: colors().muted }} truncate>
              {entry.command.description}
            </text>
          </box>
        )}
      </For>
      <text style={{ fg: colors().faint }}>
        {paletteWindow().hidden > 0
          ? `↑↓ navigate · ${paletteWindow().hidden} more · Tab complete · Esc close`
          : "↑↓ navigate · Tab complete · Esc close"}
      </text>
    </box>
  )

  const Pill = () => (
    <box
      flexDirection="column"
      width={props.width}
      border
      borderStyle="rounded"
      borderColor={props.streaming ? colors().warning : colors().border}
      backgroundColor={colors().card}
      paddingLeft={1}
      paddingRight={1}
      flexShrink={0}
    >
      <box flexDirection="row" alignItems="flex-start" minHeight={1}>
        <text style={{ fg: colors().muted }} marginRight={1} flexShrink={0}>
          +
        </text>
        <textarea
          ref={area}
          focused
          flexGrow={1}
          minWidth={8}
          minHeight={1}
          maxHeight={5}
          backgroundColor={colors().card}
          focusedBackgroundColor={colors().card}
          textColor={colors().text}
          focusedTextColor={colors().text}
          placeholder="Ask anything… (/ for commands)"
          placeholderColor={colors().muted}
          keyBindings={[
            { name: "return", action: "submit" },
            { name: "kpenter", action: "submit" },
            { name: "return", shift: true, action: "newline" },
            { name: "kpenter", shift: true, action: "newline" },
          ]}
          onSubmit={submit}
          onContentChange={handleContentChange}
          onKeyDown={onKeyDown}
        />
        <text flexShrink={1} truncate marginRight={1} onMouseDown={props.onModel}>
          <span style={{ fg: colors().text }}>
            {truncate(props.model || "no model", modelBudget())}
          </span>
          <span style={{ fg: colors().muted }}> ⌄</span>
        </text>
        <text
          bg={props.streaming ? colors().danger : props.accent.accent}
          style={{ fg: colors().page }}
          flexShrink={0}
          onMouseDown={() => (props.streaming ? props.onStop() : submit())}
        >
          <b>{props.streaming ? " ■ " : " ➤ "}</b>
        </text>
      </box>
    </box>
  )

  const StatusRow = () => (
    <box
      flexDirection="row"
      width={props.width}
      height={1}
      justifyContent="space-between"
      marginTop={1}
      flexShrink={0}
    >
      <box flexDirection="row" flexShrink={0}>
        <text
          bg={props.mode === "agent" ? props.accent.accent : colors().element}
          style={{ fg: props.mode === "agent" ? colors().page : colors().dim }}
        >
          <b>{" "}{props.mode}{" "}</b>
        </text>
        <text style={{ fg: colors().faint }}> tab mode</text>
      </box>
      <text flexShrink={0} truncate>
        <span style={{ fg: colors().faint }}>{props.provider}</span>
        <Show when={props.provider}>
          <span style={{ fg: colors().faint }}> · </span>
        </Show>
        <span style={{ fg: colors().faint }}>ctrl+k models</span>
        <Show when={props.width >= 60}>
          <span style={{ fg: colors().faint }}> · ctrl+b sidebar</span>
        </Show>
      </text>
    </box>
  )

  return (
    <box flexDirection="column" flexShrink={0} alignItems="center">
      <Show when={paletteOpen() && props.placement === "above"}>
        <Palette />
      </Show>

      <Pill />
      <StatusRow />

      <Show when={paletteOpen() && props.placement === "below"}>
        <Palette />
      </Show>
    </box>
  )
}
