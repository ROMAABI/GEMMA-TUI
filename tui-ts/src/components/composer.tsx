import { PacManCorridorBar, type AgentPhase } from "./pacman"
import { For, Show, createEffect, createMemo, createSignal, on } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import type { KeyEvent, TextareaRenderable } from "@opentui/core"
import type { AttachmentDescriptor } from "../api"
import { attachmentIcon, attachmentLabel, extractPaths, formatBytes, isDirectoryPath } from "../attachments"
import type { CommandDef } from "../api"
import type { QueuedMessage } from "../chat"
import { colors, type ResolvedTheme } from "../theme"
import { displayModelName, truncate } from "../format"

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
  attachments: AttachmentDescriptor[]
  onAttach: (path: string) => void
  onRemoveAttachment: (id: string) => void
  onPicker: () => void
  onPasteClipboard: () => Promise<string | null>
  suggestPaths: (query: string) => Promise<string[]>
  phase?: AgentPhase
  elapsed?: number
  tokensPerSecond?: number
  messageQueue?: QueuedMessage[]
  onRemoveQueueItem?: (id: string) => void
  onClearQueue?: () => void
  /** Shared with the app's global key handler so it knows what the prompt owns. */
  state?: { text: string; paletteOpen: boolean }
}) {
  const [value, setValue] = createSignal("")
  const [selected, setSelected] = createSignal(0)
  const [dismissed, setDismissed] = createSignal(false)
  let area: TextareaRenderable | undefined
  const dims = useTerminalDimensions()
  const placeholder = () =>
    dims().width < 70 ? "Ask anything…" : "Ask anything… (/ for commands)"

  const commands = createMemo(() => (props.commands.length ? props.commands : FALLBACK_COMMANDS))

  const matches = createMemo(() => {
    const text = value()
    if (!text.startsWith("/") || text.includes(" ") || text.includes("\n")) return []
    return commands().filter((command) => command.name.startsWith(text.toLowerCase()))
  })

  const paletteOpen = createMemo(() => matches().length > 0 && !dismissed())

  const [pathOptions, setPathOptions] = createSignal<string[]>([])
  const [pathSelected, setPathSelected] = createSignal(0)
  const attachQuery = createMemo(() => {
    const match = /^\/attach\s+(.*)$/.exec(value())
    return match ? match[1] ?? "" : null
  })
  const showPaths = createMemo(
    () => attachQuery() !== null && pathOptions().length > 0 && !dismissed(),
  )
  createEffect(
    on(attachQuery, (query) => {
      if (query === null) {
        if (pathOptions().length) setPathOptions([])
        return
      }
      let live = true
      void props.suggestPaths(query).then((paths) => {
        if (!live) return
        setPathOptions(paths.slice(0, 10))
        setPathSelected(0)
      })
      return () => {
        live = false
      }
    }),
  )

  // Published synchronously from the input handlers: the app's global key
  // handler runs before this component and cannot wait for a reactive effect.
  const syncState = () => {
    if (!props.state) return
    props.state.text = value()
    props.state.paletteOpen = paletteOpen()
  }
  // A freshly mounted prompt starts empty even if the previous one had a draft.
  syncState()

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
    syncState()
  }

  /** Insert clipboard text at the cursor (used by Ctrl+V). */
  const pasteText = (text: string) => {
    if (!area) return
    const at = area.cursorOffset
    const current = area.plainText
    setText(current.slice(0, at) + text + current.slice(at))
    area.cursorOffset = at + text.length
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

  const detectAndAttach = (text: string, allowRaw = true) => {
    const paths = extractPaths(text)
    // A terminal paste is arbitrary text: never turn it into an attachment
    // unless it actually contains an existing path. Palette/path completion
    // passes a path explicitly, so it keeps the raw-text fallback.
    const targets = paths.length ? paths : allowRaw ? [text.trim()].filter(Boolean) : []
    if (!targets.length) return false
    if (targets.length === 1 && (targets[0]!.endsWith("/") || isDirectoryPath(targets[0]!))) {
      setText(`/attach ${targets[0]}`)
      return true
    }
    for (const path of targets) props.onAttach(path)
    setText("")
    setDismissed(false)
    return true
  }

  const submit = () => {
    const text = area?.plainText ?? value()
    if (!text.trim()) return
    if (showPaths()) {
      detectAndAttach(pathOptions()[pathSelected()] ?? attachQuery() ?? "")
      return
    }
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
    if (event.ctrl && event.name === "v") {
      // Read the clipboard ourselves: terminals that do not send bracketed
      // paste for Ctrl+V would otherwise drop the text entirely.
      event.preventDefault()
      void props.onPasteClipboard().then((text) => {
        if (text) pasteText(text)
      })
      return
    }
    if (event.name === "backspace" && value() === "" && props.attachments.length) {
      event.preventDefault()
      props.onRemoveAttachment(props.attachments[props.attachments.length - 1]!.id)
      return
    }
    if (showPaths()) {
      if (event.name === "tab" || event.name === "return") {
        event.preventDefault()
        detectAndAttach(pathOptions()[pathSelected()] ?? attachQuery() ?? "")
        return
      }
      if (event.name === "up" || event.name === "down") {
        event.preventDefault()
        const count = pathOptions().length
        setPathSelected((prev) => (prev + (event.name === "down" ? 1 : count - 1)) % count)
        return
      }
      if (event.name === "escape") {
        event.preventDefault()
        setDismissed(true)
        syncState()
        return
      }
    }
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
      syncState()
    }
  }

  const onPaste = (event: { bytes: Uint8Array; preventDefault: () => void }) => {
    const text = new TextDecoder().decode(event.bytes)
    if (detectAndAttach(text, false)) event.preventDefault()
  }

  const handleContentChange = (_event: unknown) => {
    setValue(area?.plainText ?? "")
    setSelected(0)
    setDismissed(false)
    syncState()
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
          flexShrink={1}
          minWidth={8}
          minHeight={1}
          maxHeight={5}
          backgroundColor={colors().card}
          focusedBackgroundColor={colors().card}
          textColor={colors().text}
          focusedTextColor={colors().text}
          placeholder={placeholder()}
          placeholderColor={colors().muted}
          keyBindings={[
            { name: "return", action: "submit" },
            { name: "kpenter", action: "submit" },
            { name: "return", shift: true, action: "newline" },
            { name: "kpenter", shift: true, action: "newline" },
          ]}
          onSubmit={submit}
          onPaste={onPaste}
          onContentChange={handleContentChange}
          onKeyDown={onKeyDown}
        />
        <text flexShrink={1} truncate height={1} minWidth={0} marginRight={1} onMouseDown={props.onModel}>
          <span style={{ fg: colors().text }}>
            {truncate(displayModelName(props.model) || "no model", modelBudget())}
          </span>
          <span style={{ fg: colors().muted }}> ⌄</span>
        </text>
        <text
          bg={props.streaming ? colors().danger : props.accent.accent}
          style={{ fg: colors().page }}
          flexShrink={0}
          height={1}
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
      <Show
        when={props.streaming}
        fallback={
          <>
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
              <Show when={props.width >= 70}>
                <span style={{ fg: colors().faint }}> · ctrl+o attach</span>
                <span style={{ fg: colors().faint }}> · ctrl+b sidebar</span>
              </Show>
            </text>
          </>
        }
      >
        <PacManCorridorBar
          phase={props.phase ?? "thinking"}
          elapsed={props.elapsed ?? 0}
          width={props.width}
          tokensPerSecond={props.tokensPerSecond}
        />
      </Show>
    </box>
  )

  return (
    <box flexDirection="column" flexShrink={0} alignItems="center">
      <Show when={props.attachments.length > 0}>
        <box flexDirection="column" width={props.width} backgroundColor={colors().card} paddingLeft={1} paddingRight={1} flexShrink={0}>
          <For each={props.attachments}>
            {(attachment) => (
              <box flexDirection="row" height={1} gap={1}>
                <text style={{ fg: props.accent.accent }}>{attachmentIcon(attachment.kind)}</text>
                <text style={{ fg: colors().text }} flexGrow={1} truncate>
                  {attachmentLabel(attachment)}
                </text>
                <text style={{ fg: colors().faint }} onMouseDown={() => props.onRemoveAttachment(attachment.id)}>
                  ✕
                </text>
              </box>
            )}
          </For>
        </box>
      </Show>
      <Show when={showPaths()}>
        <box flexDirection="column" width={props.width} border borderStyle="rounded" borderColor={colors().border} backgroundColor={colors().panel} paddingLeft={1} flexShrink={0}>
          <For each={pathOptions().slice(0, 8)}>
            {(path, index) => (
              <text
                height={1}
                truncate
                style={{ fg: index() === pathSelected() ? props.accent.accent : colors().dim }}
              >
                {`${index() === pathSelected() ? "▸" : " "} ${path}`}
              </text>
            )}
          </For>
        </box>
      </Show>
      <Show when={paletteOpen() && props.placement === "above"}>
        <Palette />
      </Show>

      <Show when={(props.messageQueue?.length ?? 0) > 0}>
        <box
          flexDirection="row"
          width={props.width}
          alignItems="center"
          justifyContent="space-between"
          backgroundColor={colors().element}
          paddingLeft={1}
          paddingRight={1}
          marginBottom={1}
          flexShrink={0}
        >
          <box flexDirection="row" alignItems="center" gap={1} flexGrow={1} minWidth={0}>
            <text style={{ fg: colors().warning }}>
              <b>{`Queued (${props.messageQueue!.length}):`}</b>
            </text>
            <text style={{ fg: colors().text }} flexGrow={1} truncate>
              {`"${props.messageQueue![0]!.text}"`}
            </text>
          </box>
          <box flexDirection="row" alignItems="center" gap={1} flexShrink={0}>
            <Show when={props.width >= 60}>
              <text style={{ fg: colors().faint }}>ctrl+u clear</text>
            </Show>
            <text
              style={{ fg: colors().danger }}
              onMouseDown={() => props.onClearQueue?.()}
            >
              <b>✕ clear</b>
            </text>
          </box>
        </box>
      </Show>

      <Pill />
      <StatusRow />

      <Show when={paletteOpen() && props.placement === "below"}>
        <Palette />
      </Show>
    </box>
  )
}
