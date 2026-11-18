import { For, Match, Show, Switch, createMemo, createSignal, onMount } from "solid-js"
import { useKeyboard, useTerminalDimensions } from "@opentui/solid"
import type { InputRenderable } from "@opentui/core"
import type { AgentEvent, CommandDef, GemmaClient, ModelEntry, ModelList, Provider, ProviderCatalog } from "../api"
import { colors, previewTheme, THEMES, type ResolvedTheme, type ThemeMode } from "../theme"
import { displayModelName, truncate } from "../format"

function Frame(props: {
  title: string
  accent: ResolvedTheme
  width: number
  height?: number
  children: unknown
}) {
  const dims = useTerminalDimensions()
  const width = createMemo(() => Math.min(props.width, Math.max(20, dims().width - 4)))
  return (
    <box
      position="absolute"
      zIndex={100}
      top={Math.max(1, Math.floor((dims().height - (props.height ?? 12)) / 2))}
      left={Math.max(1, Math.floor((dims().width - width()) / 2))}
      width={width()}
      height={props.height ?? 12}
      flexDirection="column"
      backgroundColor={colors().panel}
      border
      borderStyle="rounded"
      borderColor={colors().border}
      paddingLeft={1}
      paddingRight={1}
      paddingTop={0}
      paddingBottom={0}
    >
      <text height={1}>
        <b style={{ fg: props.accent.accent }}>{props.title}</b>
      </text>
      {props.children as never}
    </box>
  )
}

const SOURCE_LABEL: Record<string, string> = {
  current: "current",
  loaded: "server",
  local: "local",
  remote: "remote",
}

function fallbackProvider(models: ModelList | null): Provider | null {
  if (!models) return null
  return {
    id: "llama.cpp",
    name: "llama.cpp (local)",
    kind: "local",
    base_url: "",
    is_default: true,
    default_model: models.active,
    models: models.models,
  }
}

type Row =
  | { kind: "gap" }
  | { kind: "provider"; provider: Provider }
  | { kind: "model"; index: number; provider: Provider; model: ModelEntry }

export function ModelPicker(props: {
  providers: ProviderCatalog | null
  models: ModelList | null
  accent: ResolvedTheme
  onSelect: (name: string, provider: string) => void
  onClose: () => void
}) {
  const rows = createMemo<Row[]>(() => {
    const list: Row[] = []
    const providers =
      props.providers?.providers ?? [fallbackProvider(props.models)].filter((item): item is Provider => item !== null)
    let index = 0
    for (const provider of providers) {
      if (list.length > 0) list.push({ kind: "gap" })
      list.push({ kind: "provider", provider })
      for (const model of provider.models) {
        list.push({ kind: "model", index: index, provider, model })
        index += 1
      }
    }
    return list
  })

  const flat = createMemo(() =>
    rows().filter((row): row is Extract<Row, { kind: "model" }> => row.kind === "model"),
  )

  const initial = () => {
    const index = flat().findIndex((row) => row.model.active)
    return index >= 0 ? index : 0
  }
  const [selected, setSelected] = createSignal(initial())

  const move = (delta: number) => {
    const count = flat().length
    if (count === 0) return
    setSelected((prev) => (prev + delta + count) % count)
  }

  const choose = () => {
    const entry = flat()[selected()]
    if (entry) props.onSelect(entry.model.name, entry.provider.id)
    props.onClose()
  }

  useKeyboard((key) => {
    if (key.name === "escape" || (key.ctrl && key.name === "c")) {
      key.preventDefault()
      props.onClose()
    } else if (key.name === "up" || key.name === "k") {
      key.preventDefault()
      move(-1)
    } else if (key.name === "down" || key.name === "j") {
      key.preventDefault()
      move(1)
    } else if (key.name === "return" || key.name === "kpenter") {
      key.preventDefault()
      choose()
    }
  })

  const modelCount = () => flat().length
  // Model rows take two lines (clean name + the full id underneath).
  const lines = createMemo(() =>
    rows().reduce((sum, row) => sum + (row.kind === "model" ? 2 : 1), 0),
  )
  const height = () => Math.min(22, Math.max(6, lines() + 4))

  return (
    <Frame title="Select model" accent={props.accent} width={66} height={height()}>
      <scrollbox height={height() - 4} flexShrink={0}>
        <Show when={rows().length > 0} fallback={<text style={{ fg: colors().muted }}>No models found</text>}>
          <For each={rows()}>
            {(row) => (
              <Switch>
                <Match when={row.kind === "gap"}>
                  <text height={1}> </text>
                </Match>
                <Match when={row.kind === "provider"}>
                  <text style={{ fg: colors().muted }} height={1}>
                    <b>{(row as Extract<Row, { kind: "provider" }>).provider.name.toUpperCase()}</b>
                  </text>
                </Match>
                <Match when={row.kind === "model"}>
                  {(() => {
                    const entry = row as Extract<Row, { kind: "model" }>
                    const active = entry.model.active
                    const isSelected = entry.index === selected()
                    return (
                      <box
                        flexDirection="column"
                        height={2}
                        backgroundColor={isSelected ? colors().element : colors().panel}
                        onMouseDown={() => {
                          setSelected(entry.index)
                          props.onSelect(entry.model.name, entry.provider.id)
                          props.onClose()
                        }}
                      >
                        <box flexDirection="row" gap={1} height={1}>
                          <text style={{ fg: isSelected ? props.accent.accent : colors().faint }}>
                            {isSelected ? "▸" : " "}
                          </text>
                          <text style={{ fg: active ? props.accent.accent : colors().text }}>
                            {active ? "●" : "○"} {truncate(displayModelName(entry.model.name), 40)}
                          </text>
                          <text style={{ fg: colors().faint }}>
                            {SOURCE_LABEL[entry.model.source] ?? entry.model.source}
                          </text>
                        </box>
                        <box flexDirection="row" gap={1} height={1}>
                          <text style={{ fg: colors().faint }}> </text>
                          <text style={{ fg: colors().faint }}>{truncate(entry.model.name, 56)}</text>
                        </box>
                      </box>
                    )
                  })()}
                </Match>
              </Switch>
            )}
          </For>
        </Show>
      </scrollbox>
      <text style={{ fg: colors().faint }} height={1}>
        {`${modelCount()} models · ↑↓ choose · Enter select · Esc close`}
      </text>
    </Frame>
  )
}

const MODES: ThemeMode[] = ["system", "dark", "light"]

export function ThemePicker(props: {
  current: string
  mode: ThemeMode
  accent: ResolvedTheme
  onSelect: (id: string) => void
  onMode: (mode: ThemeMode) => void
  onClose: () => void
}) {
  const [selected, setSelected] = createSignal(Math.max(0, THEMES.findIndex((theme) => theme.id === props.current)))

  const move = (delta: number) =>
    setSelected((prev) => (prev + delta + THEMES.length) % THEMES.length)

  const cycleMode = (delta: number) => {
    const index = MODES.indexOf(props.mode)
    props.onMode(MODES[(index + delta + MODES.length) % MODES.length]!)
  }

  const choose = () => {
    const theme = THEMES[selected()]
    if (theme) props.onSelect(theme.id)
    props.onClose()
  }

  useKeyboard((key) => {
    if (key.name === "escape") {
      key.preventDefault()
      props.onClose()
    } else if (key.name === "left") {
      key.preventDefault()
      cycleMode(-1)
    } else if (key.name === "right" || key.name === "m") {
      key.preventDefault()
      cycleMode(1)
    } else if (key.name === "up" || key.name === "k") {
      key.preventDefault()
      move(-1)
    } else if (key.name === "down" || key.name === "j") {
      key.preventDefault()
      move(1)
    } else if (key.name === "return" || key.name === "kpenter") {
      key.preventDefault()
      choose()
    }
  })

  return (
    <Frame title="Theme" accent={props.accent} width={54} height={THEMES.length + 5}>
      <box flexDirection="row" gap={1} height={1}>
        <text style={{ fg: colors().muted }}>mode</text>
        <For each={MODES}>
          {(mode) => (
            <text
              bg={mode === props.mode ? props.accent.accent : colors().element}
              style={{ fg: mode === props.mode ? colors().page : colors().dim }}
              onMouseDown={() => props.onMode(mode)}
            >
              <b>{` ${mode} `}</b>
            </text>
          )}
        </For>
        <box flexGrow={1} />
        <text style={{ fg: colors().faint }}>m cycles</text>
      </box>
      <For each={THEMES}>
        {(theme, index) => {
          const resolved = createMemo(() => previewTheme(theme.id, props.mode))
          return (
            <box
              flexDirection="row"
              gap={1}
              height={1}
              backgroundColor={index() === selected() ? colors().element : colors().panel}
              onMouseDown={() => {
                setSelected(index())
                props.onSelect(theme.id)
                props.onClose()
              }}
            >
              <text style={{ fg: index() === selected() ? resolved().accent : colors().faint }}>
                {index() === selected() ? "▸" : " "}
              </text>
              <text style={{ fg: resolved().accent }}>●</text>
              <text style={{ fg: colors().text }}>{theme.name}</text>
              <box flexGrow={1} />
              <text style={{ fg: colors().faint }}>
                {theme.system ? `terminal · ${resolved().mode}` : theme.id}
              </text>
            </box>
          )
        }}
      </For>
      <text style={{ fg: colors().faint }} height={1}>
        ↑↓ theme · ←→ or m mode · Enter apply · Esc close
      </text>
    </Frame>
  )
}

const KEYS: Array<[string, string]> = [
  ["Enter", "send message"],
  ["Shift+Enter", "new line"],
  ["Tab", "switch chat / agent"],
  ["Delete", "delete session (empty prompt)"],
  ["Ctrl+X N", "new session"],
  ["R", "rename selected session"],
  ["Ctrl+X L", "server log (/logs)"],
  ["Ctrl+X V", "expand thinking blocks"],
  ["Ctrl+X M", "model picker"],
  ["Ctrl+X T", "theme picker"],
  ["Ctrl+X A", "switch mode"],
  ["Ctrl+X B", "toggle sidebar"],
  ["Ctrl+X H", "this help"],
  ["Ctrl+X Q", "quit"],
  ["Ctrl+K", "model picker"],
  ["Ctrl+P", "command palette"],
  ["Ctrl+Y", "copy last reply"],
  ["Ctrl+W", "pair a browser"],
  ["Ctrl+C", "stop streaming"],
  ["Esc", "close palette · focus prompt"],
]

/** Settings launcher: rows that open the matching overlay or toggle in place. */
export function SettingsOverlay(props: {
  mode: string
  accent: ResolvedTheme
  onPick: (row: "model" | "theme" | "mode" | "permissions") => void
  onClose: () => void
}) {
  const rows: Array<{ id: "model" | "theme" | "mode" | "permissions"; label: string; value: string }> = [
    { id: "model", label: "Model", value: "picker" },
    { id: "theme", label: "Theme", value: "picker" },
    { id: "mode", label: "Mode", value: props.mode === "agent" ? "agent" : "chat" },
    { id: "permissions", label: "Permissions", value: "status" },
  ]
  const [selected, setSelected] = createSignal(0)

  useKeyboard((key) => {
    if (key.name === "escape") {
      key.preventDefault()
      props.onClose()
      return
    }
    if (key.name === "up") {
      key.preventDefault()
      setSelected((index) => (index + rows.length - 1) % rows.length)
      return
    }
    if (key.name === "down") {
      key.preventDefault()
      setSelected((index) => (index + 1) % rows.length)
      return
    }
    if (key.name === "return" || key.name === "kpenter") {
      key.preventDefault()
      props.onPick(rows[selected()]?.id ?? "model")
    }
  })

  return (
    <Frame title="Settings" accent={props.accent} width={44} height={9}>
      <For each={rows}>
        {(row, index) => (
          <box
            flexDirection="row"
            height={1}
            backgroundColor={selected() === index() ? colors().element : colors().panel}
            paddingLeft={1}
            paddingRight={1}
            onMouseDown={() => props.onPick(row.id)}
            onMouseOver={() => setSelected(index())}
          >
            <text style={{ fg: selected() === index() ? colors().text : colors().dim }} flexGrow={1}>
              {row.label}
            </text>
            <text style={{ fg: colors().faint }}>{row.value}</text>
          </box>
        )}
      </For>
      <text style={{ fg: colors().faint }} height={1}>
        ↑↓ select · Enter open · Esc close
      </text>
    </Frame>
  )
}

export function HelpOverlay(props: { commands: CommandDef[]; accent: ResolvedTheme; onClose: () => void }) {  useKeyboard((key) => {
    if (key.name === "escape" || key.name === "return" || key.name === "kpenter" || key.name === "q") {
      key.preventDefault()
      props.onClose()
    }
  })

  return (
    <Frame title="Commands & keys" accent={props.accent} width={76} height={22}>
      <box flexDirection="row" gap={2} flexGrow={1}>
        <box flexDirection="column" width="44%">
          <text style={{ fg: colors().muted }}>COMMANDS</text>
          <For each={props.commands}>
            {(command) => (
              <text height={1}>
                <span style={{ fg: props.accent.accent }}>{command.name.padEnd(10)}</span>
                <span style={{ fg: colors().muted }}>{truncate(command.description, 22)}</span>
              </text>
            )}
          </For>
        </box>
        <box flexDirection="column" flexGrow={1}>
          <text style={{ fg: colors().muted }}>KEYS</text>
          <For each={KEYS}>
            {([key, description]) => (
              <text height={1}>
                <span style={{ fg: colors().text }}>{key.padEnd(14)}</span>
                <span style={{ fg: colors().muted }}>{description}</span>
              </text>
            )}
          </For>
        </box>
      </box>
      <text style={{ fg: colors().faint }} height={1}>
        Esc or Enter to close
      </text>
    </Frame>
  )
}

export function RenameOverlay(props: {
  initial: string
  accent: ResolvedTheme
  onSubmit: (title: string) => void
  onClose: () => void
}) {
  let input: InputRenderable | undefined

  useKeyboard((key) => {
    if (key.name === "escape") {
      key.preventDefault()
      props.onClose()
    }
  })

  return (
    <Frame title="Rename session" accent={props.accent} width={60} height={7}>
      <text style={{ fg: colors().faint }}>New title</text>
      <box
        marginTop={1}
        border
        borderStyle="rounded"
        borderColor={colors().border}
        backgroundColor={colors().card}
        paddingLeft={1}
        paddingRight={1}
      >
        <input
          ref={input}
          focused
          value={props.initial}
          placeholder="Session title"
          backgroundColor={colors().card}
          focusedBackgroundColor={colors().card}
          textColor={colors().text}
          focusedTextColor={colors().text}
          placeholderColor={colors().muted}
          onSubmit={() => {
            props.onSubmit(input?.value ?? "")
            props.onClose()
          }}
        />
      </box>
      <text style={{ fg: colors().faint }}>Enter save · Esc cancel</text>
    </Frame>
  )
}

/** Approval dialog for a tool call: allow once, always, or deny. */
export function PermissionOverlay(props: {
  request: AgentEvent
  accent: ResolvedTheme
  onDecide: (decision: "allow" | "always" | "deny") => void
}) {
  const [selected, setSelected] = createSignal(0)
  const options: Array<{ label: string; decision: "allow" | "always" | "deny" }> = [
    { label: "Allow once", decision: "allow" },
    { label: "Allow for session", decision: "always" },
    { label: "Deny", decision: "deny" },
  ]

  const choose = (index = selected()) => props.onDecide(options[index]?.decision ?? "deny")

  useKeyboard((key) => {
    if ((key.ctrl && key.name === "c") || key.name === "escape" || key.name === "n") {
      key.preventDefault()
      choose(2)
    } else if (key.name === "y") {
      key.preventDefault()
      choose(0)
    } else if (key.name === "a") {
      key.preventDefault()
      choose(1)
    } else if (key.name === "left" || key.name === "up") {
      key.preventDefault()
      setSelected((prev) => (prev + options.length - 1) % options.length)
    } else if (key.name === "right" || key.name === "down" || key.name === "tab") {
      key.preventDefault()
      setSelected((prev) => (prev + 1) % options.length)
    } else if (key.name === "return" || key.name === "kpenter") {
      key.preventDefault()
      choose()
    }
  })

  const args = () => {
    try {
      const text = JSON.stringify(props.request.args ?? {}, null, 2)
      return text.length > 300 ? `${text.slice(0, 299)}…` : text
    } catch {
      return ""
    }
  }

  return (
    <Frame title="⚠ Permission required" accent={props.accent} width={72} height={16}>
      <text style={{ fg: colors().text }} height={1}>
        <b>{props.request.tool || props.request.action}</b>
      </text>
      <text style={{ fg: colors().dim }} height={1} truncate>
        {props.request.summary ?? ""}
      </text>
      <Show when={props.request.reason}>
        <text style={{ fg: colors().faint }} height={1} truncate>
          {props.request.reason}
        </text>
      </Show>
      <scrollbox height={6} marginTop={1} flexShrink={0}>
        <text style={{ fg: colors().muted }} wrapMode="word">
          {args()}
        </text>
      </scrollbox>
      <box flexDirection="row" gap={1} height={1} marginTop={1}>
        <For each={options}>
          {(option, index) => (
            <text
              bg={index() === selected() ? props.accent.accent : colors().element}
              style={{ fg: index() === selected() ? colors().page : colors().dim }}
              onMouseDown={() => choose(index())}
            >
              <b>{` ${option.label} `}</b>
            </text>
          )}
        </For>
      </box>
      <text style={{ fg: colors().faint }} height={1}>
        y allow · a always · n deny · ←→ choose
      </text>
    </Frame>
  )
}

/** Tail of ~/.local/state/gemma/server.log (the TUI owns the terminal). */
export function LogsOverlay(props: {
  client: GemmaClient
  accent: ResolvedTheme
  onClose: () => void
}) {
  const [text, setText] = createSignal("Loading…")
  const [path, setPath] = createSignal("")
  const [error, setError] = createSignal("")

  useKeyboard((key) => {
    if (key.name === "escape" || key.name === "return" || key.name === "kpenter" || key.name === "q") {
      key.preventDefault()
      props.onClose()
    }
  })

  onMount(() => {
    props.client
      .logs()
      .then((result) => {
        setPath(result.path)
        setText(result.text.trim() || "(log file is empty)")
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
  })

  return (
    <Frame title="Server log (tail)" accent={props.accent} width={90} height={24}>
      <text style={{ fg: colors().faint }} height={1} truncate>
        {path()}
      </text>
      <scrollbox flexGrow={1} verticalScrollbarOptions={{ visible: true }}>
        <text style={{ fg: error() ? colors().danger : colors().muted }} wrapMode="word">
          {error() || text()}
        </text>
      </scrollbox>
      <text style={{ fg: colors().faint }} height={1}>
        Esc, Enter or q to close
      </text>
    </Frame>
  )
}
