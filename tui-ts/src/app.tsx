import { Show, createEffect, createMemo, createSignal, on, onCleanup } from "solid-js"
import { useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/solid"
import type { Chat } from "./chat"
import { mergeCommands } from "./commands"
import { activeThemeMode, colors, isThemeMode, setTheme, setThemeMode, theme, THEMES } from "./theme"
import { loadPrefs, savePrefs } from "./prefs"
import { Feed } from "./components/feed"
import { Sidebar, SIDEBAR_WIDTH } from "./components/sidebar"
import { Header } from "./components/topbar"
import { Landing } from "./components/landing"
import { Composer, type Inject } from "./components/composer"
import { StatusBar } from "./components/statusbar"
import {
  ModelPicker,
  HelpOverlay,
  RenameOverlay,
  ThemePicker,
  PermissionOverlay,
} from "./components/overlays"

type Overlay =
  | null
  | { kind: "model" }
  | { kind: "theme" }
  | { kind: "help" }
  | { kind: "rename"; initial: string }

/** Commands that still belong to the Textual TUI. */
const CLASSIC_ONLY = new Set(["/websearch"])

export function App(props: { chat: Chat }) {
  const renderer = useRenderer()
  const dims = useTerminalDimensions()

  const [sidebar, setSidebar] = createSignal(true)
  const [overlay, setOverlay] = createSignal<Overlay>(null)
  const [focusSignal, setFocusSignal] = createSignal(0)
  const [inject, setInject] = createSignal<Inject | null>(null)
  const [flashNotice, setFlashNotice] = createSignal<string | null>(null)
  const [pairLink, setPairLink] = createSignal<string | null>(null)
  const [leader, setLeader] = createSignal(false)

  const activeSession = createMemo(() => props.chat.activeSession())
  const streaming = createMemo(() => props.chat.streaming())
  const metrics = createMemo(() => props.chat.metrics())
  const info = createMemo(() => props.chat.info())
  const models = createMemo(() => props.chat.models())
  const providers = createMemo(() => props.chat.providers())
  const sessions = createMemo(() => props.chat.sessions())
  const commands = createMemo(() => mergeCommands(props.chat.commands()))
  const accent = theme
  const mode = createMemo(() => props.chat.mode())
  const notice = () => flashNotice() ?? props.chat.notice()
  const landing = createMemo(() => (activeSession()?.messages.length ?? 0) === 0)

  let noticeTimer: ReturnType<typeof setTimeout> | undefined
  let leaderTimer: ReturnType<typeof setTimeout> | undefined
  onCleanup(() => {
    if (noticeTimer) clearTimeout(noticeTimer)
    if (leaderTimer) clearTimeout(leaderTimer)
  })

  // Narrow terminals drop the sidebar automatically.
  let manualSidebar = false
  let wasBelow = false
  createEffect(
    on(
      () => dims().width,
      (width) => {
        const below = width < 92
        if (below !== wasBelow) {
          wasBelow = below
          manualSidebar = false
        }
        if (!manualSidebar) setSidebar(!below)
      },
    ),
  )

  const toggleSidebar = () => {
    manualSidebar = true
    setSidebar((prev) => !prev)
  }

  const setSidebarVisible = (next: boolean) => {
    manualSidebar = true
    setSidebar(next)
  }

  const flash = (text: string) => {
    setFlashNotice(text)
    if (noticeTimer) clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => setFlashNotice(null), 6000)
  }

  const setAccent = (id: string) => {
    setTheme(id)
    savePrefs({ ...loadPrefs(), theme: id })
  }

  const setMode = (mode: "system" | "dark" | "light") => {
    setThemeMode(mode)
    savePrefs({ ...loadPrefs(), themeMode: mode })
  }

  const injectText = (text: string) => {
    setInject({ id: (inject()?.id ?? 0) + 1, text })
    setFocusSignal((prev) => prev + 1)
  }

  const copyText = (text: string) => {
    if (!text) {
      flash("Nothing to copy")
      return
    }
    const copied = renderer.copyToClipboardOSC52(text)
    flash(copied ? "Copied to the clipboard (OSC 52)" : "This terminal cannot copy to the clipboard")
  }

  const copyLastReply = () => {
    const session = activeSession()
    const reply = [...(session?.messages ?? [])].reverse().find((message) => message.role === "assistant")
    if (!reply?.content) {
      flash("No reply to copy yet")
      return
    }
    copyText(reply.content)
  }

  const pairBrowser = () => {
    props.chat.client
      .webLink()
      .then((url) => {
        setPairLink(url)
        flash(`Pair a browser: ${url}`)
      })
      .catch((error: unknown) => flash(error instanceof Error ? error.message : String(error)))
  }

  const quit = () => {
    try {
      savePrefs({ theme: accent().id, themeMode: activeThemeMode(), sessionId: props.chat.activeId() })
    } catch {
      // Preferences are best effort.
    }
    renderer.destroy()
    setTimeout(() => process.exit(0), 20)
  }

  const deleteActive = () => {
    const session = activeSession()
    if (session) void props.chat.remove(session.id)
    setOverlay(null)
  }

  const startLeader = () => {
    setLeader(true)
    if (leaderTimer) clearTimeout(leaderTimer)
    leaderTimer = setTimeout(() => setLeader(false), 1600)
  }

  const runLeader = (name: string) => {
    switch (name) {
      case "n":
        void props.chat.newChat()
        break
      case "l":
        toggleSidebar()
        break
      case "m":
        if (!streaming()) setOverlay({ kind: "model" })
        break
      case "t":
        setOverlay({ kind: "theme" })
        break
      case "a":
        props.chat.toggleMode()
        break
      case "b":
        toggleSidebar()
        break
      case "h":
        setOverlay({ kind: "help" })
        break
      case "c":
        void props.chat.clearChat()
        break
      case "y":
        copyLastReply()
        break
      case "w":
        pairBrowser()
        break
      case "q":
        quit()
        break
      default:
        flash(`Unknown leader key: Ctrl+X ${name.toUpperCase()}`)
    }
  }

  // ── Keyboard dispatch (global, runs before the focused textarea) ───
  useKeyboard((key) => {
    if (props.chat.permission()) return // the permission dialog owns its keys
    if (overlay()) return // overlays own their keys

    const ctrl = key.ctrl === true
    const name = key.name

    if (leader()) {
      key.preventDefault()
      setLeader(false)
      if (leaderTimer) clearTimeout(leaderTimer)
      runLeader(name)
      return
    }

    if (ctrl && name === "x") {
      key.preventDefault()
      startLeader()
      return
    }

    if (ctrl && (name === "d" || name === "q")) {
      key.preventDefault()
      quit()
      return
    }

    if (ctrl && name === "c") {
      key.preventDefault()
      if (streaming()) props.chat.stop()
      else flash("Ctrl+D or Ctrl+Q quits")
      return
    }

    if (ctrl && name === "b") {
      key.preventDefault()
      toggleSidebar()
      return
    }

    if (ctrl && name === "n") {
      key.preventDefault()
      void props.chat.newChat()
      return
    }

    if (ctrl && name === "l") {
      key.preventDefault()
      void props.chat.clearChat()
      return
    }

    if (ctrl && name === "t") {
      key.preventDefault()
      setOverlay({ kind: "theme" })
      return
    }

    if (ctrl && name === "y") {
      key.preventDefault()
      copyLastReply()
      return
    }

    if (ctrl && name === "w") {
      key.preventDefault()
      pairBrowser()
      return
    }

    if (ctrl && name === "p") {
      key.preventDefault()
      injectText("/")
      return
    }

    if (ctrl && name === "k") {
      key.preventDefault()
      if (!streaming()) setOverlay({ kind: "model" })
      return
    }

    if (name === "f1" || (ctrl && name === "h")) {
      key.preventDefault()
      setOverlay({ kind: "help" })
      return
    }

    if (name === "escape") {
      key.preventDefault()
      setFocusSignal((prev) => prev + 1)
    }
  })

  // ── Command dispatch ──────────────────────────────────────────────
  const handleCommand = (input: string): boolean => {
    const text = input.trim()
    if (!text.startsWith("/")) return false
    const [rawName, ...rest] = text.split(/\s+/)
    const name = (rawName ?? "").toLowerCase()
    const arg = rest.join(" ").trim()

    switch (name) {
      case "/new":
        void props.chat.newChat()
        return true
      case "/clear":
        void props.chat.clearChat()
        return true
      case "/model":
        if (arg) void props.chat.setModel(arg)
        else setOverlay({ kind: "model" })
        return true
      case "/mode":
        if (arg === "chat" || arg === "agent") void props.chat.setMode(arg)
        else props.chat.toggleMode()
        return true
      case "/agent":
        void props.chat.setMode("agent")
        return true
      case "/chat":
        void props.chat.setMode("chat")
        return true
      case "/theme": {
        const [first, ...rest] = arg.split(/\s+/).filter(Boolean)
        if (first === "mode") {
          const next = rest[0]
          if (isThemeMode(next)) setMode(next)
          else flash("Theme mode: system, dark or light")
          return true
        }
        if (first && THEMES.some((candidate) => candidate.id === first)) {
          setAccent(first)
          flash(`Theme: ${first}`)
          return true
        }
        if (isThemeMode(first)) {
          setMode(first)
          flash(`Theme mode: ${first}`)
          return true
        }
        if (first) {
          flash(`Unknown theme ${first} — try ${THEMES.map((candidate) => candidate.id).join(", ")}`)
          return true
        }
        setOverlay({ kind: "theme" })
        return true
      }
      case "/rename":
        setOverlay({ kind: "rename", initial: activeSession()?.title ?? "" })
        return true
      case "/delete":
        deleteActive()
        return true
      case "/copy":
        copyLastReply()
        return true
      case "/providers": {
        const names = (providers()?.providers ?? []).map((provider) => provider.name)
        flash(names.length ? names.join(" · ") : "No provider catalog available")
        return true
      }
      case "/stats": {
        const m = metrics()
        const s = activeSession()
        flash(
          `session ${s ? s.id.slice(0, 8) : "—"} · ${s?.messages.length ?? 0} msg · ctx ${m.contextUsed}/${m.contextMax} · ${m.tokens} tok · ${m.elapsed.toFixed(1)}s · ${m.tokPerSec.toFixed(1)} tok/s`,
        )
        return true
      }
      case "/sidebar": {
        if (arg === "show") setSidebarVisible(true)
        else if (arg === "hide") setSidebarVisible(false)
        else toggleSidebar()
        flash(`Sidebar ${sidebar() ? "shown" : "hidden"}`)
        return true
      }
      case "/history": {
        const all = sessions()
        if (!all.length) {
          flash("No saved sessions yet")
          return true
        }
        const shown = all.slice(0, 8)
        flash(
          shown
            .map((session) => {
              const when = new Date(session.updatedAt)
              const stamp = Number.isNaN(when.getTime())
                ? "?"
                : when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
              return `${session.title} (${session.messages.length} msg, ${stamp})`
            })
            .join(" · ") + (all.length > shown.length ? ` · +${all.length - shown.length} more` : ""),
        )
        return true
      }
      case "/config": {
        const i = info()
        if (!i) {
          flash("Config unavailable — server not connected")
          return true
        }
        flash(
          `model ${i.model} · provider ${i.provider} · mode ${i.mode} · temp ${i.temperature} · ctx ${i.context_max.toLocaleString()} · provider ${i.provider_mode ?? "auto"} · perms ${i.permission_mode ?? "auto"}`,
        )
        return true
      }
      case "/web":
        pairBrowser()
        return true
      case "/classic":
        flash("The Textual UI is still available: gemma --classic")
        return true
      case "/help":
        setOverlay({ kind: "help" })
        return true
      case "/exit":
      case "/quit":
        quit()
        return true
      default:
        flash(
          CLASSIC_ONLY.has(name)
            ? `${name} lives in the classic TUI: gemma --classic`
            : `Unknown command ${name} — try /help`,
        )
        return true
    }
  }

  const handleSend = (text: string) => {
    if (handleCommand(text)) return
    void props.chat.send(text)
  }

  const contentWidth = () =>
    Math.max(20, dims().width - (sidebar() ? SIDEBAR_WIDTH + 1 : 0))

  /** Gemini-style pill: 70% of the main area, clamped to a sane range. */
  const pillWidth = () => {
    const main = contentWidth()
    return Math.min(main - 4, Math.max(46, Math.round(main * 0.7)))
  }

  const ComposerBlock = (blockProps: { placement: "above" | "below" }) => (
    <Composer
      accent={accent()}
      commands={commands()}
      streaming={streaming()}
      mode={mode()}
      model={info()?.model ?? ""}
      provider={info()?.provider ?? ""}
      active={overlay() === null && props.chat.permission() === null}
      width={pillWidth()}
      placement={blockProps.placement}
      focusSignal={focusSignal()}
      inject={inject()}
      onSend={handleSend}
      onStop={() => props.chat.stop()}
      onCommand={handleCommand}
      onToggleMode={() => props.chat.toggleMode()}
      onModel={() => {
        if (!streaming()) setOverlay({ kind: "model" })
      }}
    />
  )

  return (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      backgroundColor={colors().page}
    >
      <box
        flexDirection="row"
        flexGrow={1}
        minHeight={0}
      >
        <Show when={sidebar()}>
          <Sidebar
            sessions={props.chat.sessions()}
            activeId={props.chat.activeId()}
            accent={accent()}
            streaming={streaming()}
            onNew={() => void props.chat.newChat()}
            onSelect={(id) => props.chat.select(id)}
          />
        </Show>

        <box
          flexDirection="column"
          flexGrow={1}
          minWidth={0}
        >
          <Header
            title={activeSession()?.title ?? "New session"}
            model={info()?.model ?? ""}
            width={contentWidth()}
          />

          <Show when={notice()}>
            <box flexShrink={0} paddingLeft={2} paddingRight={2} height={1}>
              <text
                style={{ fg: colors().dim }}
                truncate
                onMouseDown={() => {
                  const link = pairLink()
                  if (link && (notice() ?? "").includes("Pair a browser")) copyText(link)
                }}
              >
                {notice()}
              </text>
            </box>
          </Show>

          <Show when={leader()}>
            <box flexShrink={0} paddingLeft={2} paddingRight={2} height={1}>
              <text style={{ fg: accent().accent }}>
                <b>ctrl+x</b>
                <span style={{ fg: colors().faint }}> n new · l list · m models · t theme · a mode · h help · q quit</span>
              </text>
            </box>
          </Show>

          <Show
            when={landing()}
            fallback={
              <box flexDirection="column" flexGrow={1} minHeight={0}>
                <Feed
                  chat={props.chat}
                  accent={accent().accent}
                  model={info()?.model ?? ""}
                  onCopy={copyText}
                />
                <ComposerBlock placement="above" />
                <StatusBar metrics={metrics()} width={contentWidth()} version={info()?.version} />
              </box>
            }
          >
            <box flexDirection="column" flexGrow={1} minHeight={0}>
              <Landing>
                <ComposerBlock placement="below" />
              </Landing>
              <StatusBar metrics={metrics()} width={contentWidth()} version={info()?.version} />
            </box>
          </Show>
        </box>
      </box>

      <Show when={overlay()?.kind === "model"}>
        <ModelPicker
          providers={providers()}
          models={models()}
          accent={accent()}
          onSelect={(name, provider) => void props.chat.setModel(name, provider)}
          onClose={() => setOverlay(null)}
        />
      </Show>
      <Show when={overlay()?.kind === "theme"}>
        <ThemePicker
          current={accent().id}
          mode={activeThemeMode()}
          accent={accent()}
          onSelect={setAccent}
          onMode={setMode}
          onClose={() => setOverlay(null)}
        />
      </Show>
      <Show when={overlay()?.kind === "help"}>
        <HelpOverlay commands={commands()} accent={accent()} onClose={() => setOverlay(null)} />
      </Show>
      <Show when={overlay()?.kind === "rename" ? (overlay() as { kind: "rename"; initial: string }) : null}>
        {(current: () => { kind: "rename"; initial: string }) => (
          <RenameOverlay
            initial={current().initial}
            accent={accent()}
            onSubmit={(title) => void props.chat.renameChat(title)}
            onClose={() => setOverlay(null)}
          />
        )}
      </Show>
      <Show when={props.chat.permission()}>
        {(request: () => NonNullable<ReturnType<Chat["permission"]>>) => (
          <PermissionOverlay
            request={request()}
            accent={accent()}
            onDecide={(decision) => void props.chat.respondPermission(decision)}
          />
        )}
      </Show>
    </box>
  )
}
