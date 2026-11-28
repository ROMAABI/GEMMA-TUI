import { Show, createEffect, createMemo, createSignal, on, onCleanup, untrack } from "solid-js"
import { useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/solid"
import type { Chat } from "./chat"
import { mergeCommands } from "./commands"
import { debugLog } from "./debuglog"
import { displayModelName } from "./format"
import { activeThemeMode, colors, isThemeMode, setTheme, setThemeMode, theme, THEMES } from "./theme"
import { loadPrefs, savePrefs } from "./prefs"
import { Feed } from "./components/feed"
import { Sidebar } from "./components/sidebar"
import { LAYOUT, SIDEBAR_HIDE_BELOW, SIDEBAR_WIDTH } from "./layout"
import { Header } from "./components/topbar"
import { Landing } from "./components/landing"
import { Composer, type Inject } from "./components/composer"
import { StatusBar } from "./components/statusbar"
import { deriveAgentState } from "./components/agent-state"
import {
  ModelPicker,
  HelpOverlay,
  RenameOverlay,
  ThemePicker,
  SettingsOverlay,
  PermissionOverlay,
  LogsOverlay,
  FilePickerOverlay,
} from "./components/overlays"

type Overlay =
  | null
  | { kind: "picker" }
  | { kind: "logs" }
  | { kind: "model" }
  | { kind: "theme" }
  | { kind: "help" }
  | { kind: "settings" }
  | { kind: "rename"; initial: string }

export function App(props: { chat: Chat }) {
  const renderer = useRenderer()
  const dims = useTerminalDimensions()

  const [sidebar, setSidebar] = createSignal(true)
  const [sidebarPicked, setSidebarPicked] = createSignal(false)
  const [overlay, setOverlay] = createSignal<Overlay>(null)
  const [focusSignal, setFocusSignal] = createSignal(0)
  const [inject, setInject] = createSignal<Inject | null>(null)
  const [flashNotice, setFlashNotice] = createSignal<string | null>(null)
  const [pairLink, setPairLink] = createSignal<string | null>(null)
  const [leader, setLeader] = createSignal(false)
  const composerState = { text: "", paletteOpen: false }

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

  // Narrow terminals drop the sidebar automatically; a manual ctrl+b choice
  // wins and survives later resizes.
  // No manual choice at startup: the first resize/width read applies auto-hide.
  let manualSidebar = false
  createEffect(
    on(
      () => [dims().width, dims().height] as const,
      ([width]) => {
        if (!manualSidebar) setSidebar(width >= SIDEBAR_HIDE_BELOW)
        debugLog(
          `cols=${width} sidebar=${untrack(sidebar) ? "shown" : "hidden"}` +
            (manualSidebar ? " manual" : ""),
        )
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

  // On a narrow terminal the sidebar floats above the chat instead of
  // squeezing it, and selecting a session dismisses it.
  const closeSidebarOverlay = () => {
    manualSidebar = false
    setSidebar(false)
  }
  const sidebarOverlay = () => sidebar() && dims().width < SIDEBAR_HIDE_BELOW
  const sidebarInline = () => sidebar() && !sidebarOverlay()

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

  const openModelPicker = () => {
    if (streaming()) flash("Stop the current reply before switching models")
    else setOverlay({ kind: "model" })
  }

  const runLeader = (name: string) => {
    switch (name) {
      case "s":
        if (streaming()) {
          props.chat.stop()
          flash("Stopping…")
        } else flash("Nothing is running")
        break
      case "n":
        void props.chat.newChat()
        break
      case "l":
        setOverlay({ kind: "logs" })
        break
      case "v":
        props.chat.toggleThinking()
        flash(props.chat.showThinking() ? "Thinking expanded" : "Thinking collapsed")
        break
      case "m":
        openModelPicker()
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
    if (sidebarOverlay() && key.name === "escape") {
      key.preventDefault()
      closeSidebarOverlay()
      return
    }

    const ctrl = key.ctrl === true
    const name = key.name
    const composer = composerState
    if (name !== "r") setSidebarPicked(false)

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
      if (streaming()) {
        try {
          props.chat.stop()
          flash("Stopped — the reply so far is kept")
        } catch {
          flash("Could not stop the run — try /stop")
        }
      } else flash("Ctrl+D or Ctrl+Q quits")
      return
    }

    if (ctrl && name === "u") {
      if (props.chat.messageQueue().length > 0) {
        key.preventDefault()
        props.chat.clearQueue()
        flash("Message queue cleared")
        return
      }
    }

    if (ctrl && name === "o") {
      key.preventDefault()
      setOverlay({ kind: "picker" })
      return
    }

    if (ctrl && name === "b") {
      key.preventDefault()
      toggleSidebar()
      return
    }

    if (ctrl && name === "n") {
      // While the slash palette is open, Ctrl+N walks the list instead.
      if (composer.paletteOpen) return
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
      // The palette owns Ctrl+P as "previous entry" once it is open.
      if (composer.paletteOpen) return
      key.preventDefault()
      injectText("/")
      return
    }

    if (ctrl && name === "k") {
      key.preventDefault()
      openModelPicker()
      return
    }

    if (name === "f1" || (ctrl && name === "h")) {
      key.preventDefault()
      setOverlay({ kind: "help" })
      return
    }

    if (
      !ctrl &&
      name === "r" &&
      sidebar() &&
      composer.text.length === 0 &&
      !composer.paletteOpen &&
      sidebarPicked() &&
      (activeSession()?.messages.length ?? 0) > 0
    ) {
      // Rename the selected sidebar session.
      key.preventDefault()
      setOverlay({ kind: "rename", initial: activeSession()?.title ?? "" })
      return
    }

    if (name === "delete") {
      // Delete edits the draft; only an empty prompt deletes the session.
      if (composer.text.length > 0) return
      key.preventDefault()
      deleteActive()
      return
    }

    if (name === "escape") {
      // The composer closes its own slash palette on Escape.
      if (composer.paletteOpen) return
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
      case "/attach":
        if (arg.trim()) void props.chat.addAttachment(arg.trim())
        else setOverlay({ kind: "picker" })
        return true
      case "/stop":
        if (streaming()) {
          props.chat.stop()
          flash("Stopping…")
        } else flash("Nothing is running")
        return true
      case "/queue":
        if (arg === "clear") {
          props.chat.clearQueue()
          flash("Message queue cleared")
        } else {
          const q = props.chat.messageQueue()
          flash(q.length ? `Queue (${q.length}): ${q.map((m) => `"${m.text}"`).join(", ")}` : "Queue is empty")
        }
        return true
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
      case "/agent": {
        const [first, ...rest] = arg.split(/\s+/).filter(Boolean)
        const value = rest.join(" ")
        if (!first) {
          void props.chat.setMode("agent")
          return true
        }
        if (first === "status") {
          const i = info()
          if (!i) {
            flash("Agent status unavailable — server not connected")
            return true
          }
          flash(
            `agent ${i.mode === "agent" ? "active" : "inactive"} · ws ${i.workspace} · steps ${i.max_steps ?? 10} · provider ${i.provider_mode ?? "auto"} · perms ${i.permission_mode ?? "auto"} · ctx ${(i.context_max ?? 0).toLocaleString()}`,
          )
          return true
        }
        if (first === "dir") {
          if (!value) {
            flash(`Agent workspace: ${info()?.workspace ?? "?"} — usage /agent dir <path>`)
            return true
          }
          void props.chat.setAgentSettings({ workspace: value })
          return true
        }
        if (first === "steps") {
          const steps = Number.parseInt(value, 10)
          if (!Number.isFinite(steps) || steps < 1 || steps > 50) {
            flash(`Invalid steps: ${value} — must be 1..50`)
            return true
          }
          void props.chat.setAgentSettings({ max_steps: steps })
          return true
        }
        if (first === "on") {
          void props.chat.setMode("agent")
          return true
        }
        if (first === "off") {
          void props.chat.setMode("chat")
          return true
        }
        flash("Usage: /agent [on|off|status|dir <path>|steps <1..50>]")
        return true
      }
      case "/chat":
        void props.chat.setMode("chat")
        return true
      case "/mcp":
      case "/skills":
      case "/lsp": {
        const service = name.slice(1) as "mcp" | "skills" | "lsp"
        const sub = arg.toLowerCase() || "status"
        const valid: Record<string, string[]> = {
          mcp: ["status", "start", "reload", "stop"],
          skills: ["list", "reload", "status"],
          lsp: ["status", "stop"],
        }
        if (!valid[service]!.includes(sub)) {
          flash(`Usage: /${service} ${valid[service]!.join("|")}`)
          return true
        }
        void (async () => {
          const status = await props.chat.serviceStatus(service, sub === "status" ? "status" : sub)
          if (status) flash(status.replace(/\n+/g, " · "))
        })()
        return true
      }
      case "/permissions": {
        const [first, ...rest] = arg.split(/\s+/).filter(Boolean)
        const effect = first?.toLowerCase()
        if (!first || effect === "status") {
          void (async () => {
            const config = await props.chat.permissionConfig()
            if (config) {
              const last = config.rules[config.rules.length - 1] ?? "none"
              flash(`Permission mode: ${config.mode} · ${config.rules.length} user rules · last: ${last}`)
            }
          })()
          return true
        }
        if (effect === "mode") {
          const next = rest[0]?.toLowerCase()
          if (next !== "auto" && next !== "ask" && next !== "readonly") {
            flash("Usage: /permissions mode auto|ask|readonly")
            return true
          }
          void (async () => {
            const config = await props.chat.updatePermissions({ mode: next })
            if (config) flash(`Permission mode set to ${config.mode}`)
          })()
          return true
        }
        if (effect === "allow" || effect === "deny" || effect === "ask") {
          const action = rest[0] ?? "*"
          const resource = rest.slice(1).join(" ") || "*"
          void (async () => {
            const config = await props.chat.updatePermissions({
              add: "rule",
              effect,
              action,
              resource,
            })
            if (config) flash(`Added rule ${action}:${resource}:${effect}`)
          })()
          return true
        }
        if (effect === "reset") {
          void (async () => {
            const config = await props.chat.updatePermissions({ reset: true })
            if (config) flash("Permission rules reset to defaults")
          })()
          return true
        }
        flash("Usage: /permissions [status|mode auto|ask|readonly|allow|deny|ask|reset]")
        return true
      }
      case "/provider": {
        const next = arg.toLowerCase()
        if (next === "auto" || next === "native" || next === "prompt") {
          void props.chat.setProviderMode(next)
          return true
        }
        flash(
          `Provider mode: ${info()?.provider_mode ?? "auto"} — usage /provider auto|native|prompt (native = tool calling, prompt = XML fallback)`,
        )
        return true
      }
      case "/context": {
        if (arg) {
          const limit = Number.parseInt(arg, 10)
          if (!Number.isFinite(limit)) {
            flash("Usage: /context <token limit>")
            return true
          }
          void props.chat.setContextLimit(limit)
          return true
        }
        const i = info()
        flash(
          i
            ? `Context limit: ${i.context_max.toLocaleString()} tokens · workspace ${i.workspace}`
            : "Context unavailable — server not connected",
        )
        return true
      }
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
        if (arg.trim()) {
          void props.chat.renameChat(arg.trim())
          flash(`Renamed to ${arg.trim().slice(0, 24)}`)
          return true
        }
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
      case "/logs": {
        setOverlay({ kind: "logs" })
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
      case "/system": {
        if (!arg) {
          flash(`System prompt: ${props.chat.systemPrompt().slice(0, 160)}…`)
        } else {
          props.chat.setSystemPrompt(arg)
          flash("System prompt updated and saved")
        }
        return true
      }
      case "/config": {
        const i = info()
        if (!i) {
          flash("Config unavailable — server not connected")
          return true
        }
        flash(
          `model ${displayModelName(i.model)} · provider ${i.provider} · mode ${i.mode} · temp ${i.temperature} · ctx ${i.context_max.toLocaleString()} · provider ${i.provider_mode ?? "auto"} · perms ${i.permission_mode ?? "auto"}`,
        )
        return true
      }
      case "/web":
        pairBrowser()
        return true
      case "/help":
        setOverlay({ kind: "help" })
        return true
      case "/exit":
      case "/quit":
        quit()
        return true
      default:
        flash(`Unknown command ${name} — try /help`)
        return true
      case "/init":
        void (async () => {
          try {
            const result = await props.chat.client.initWorkspace()
            flash(result.ok ? `AGENTS.md created at ${result.path}` : "AGENTS.md already exists")
          } catch (error) {
            flash(error instanceof Error ? error.message : String(error))
          }
        })()
        return true
      case "/compact":
        void props.chat.compactChat(arg)
        return true
      case "/search": {
        // Quick toggle for auto web search (same signal /websearch sets).
        if (arg === "on" || arg === "off") {
          props.chat.setWebsearch(arg === "on")
          flash(`Web search: ${arg.toUpperCase()}`)
          return true
        }
        flash(`Web search: ${props.chat.websearch() ? "ON" : "OFF"} — usage /search on|off`)
        return true
      }
      case "/websearch": {
        const [first, ...rest] = arg.split(/\s+/).filter(Boolean)
        const value = rest.join(" ")
        if (!first || first === "status") {
          void (async () => {
            const state = await props.chat.client.researchStatus()
            flash(
              `Web search ${props.chat.websearch() ? "ON" : "OFF"} · ${state.healthy ? "🟢" : "🔴"} ${state.detail} · ${state.url} · deep read ${state.deep_read ? "on" : "off"}`,
            )
          })()
          return true
        }
        if (first === "on" || first === "true" || first === "1") {
          props.chat.setWebsearch(true)
          flash("Web search: ON")
          return true
        }
        if (first === "off" || first === "false" || first === "0") {
          props.chat.setWebsearch(false)
          flash("Web search: OFF")
          return true
        }
        if (first === "url") {
          if (!value) {
            flash("Usage: /websearch url <http://host:port>")
            return true
          }
          void (async () => {
            const result = await props.chat.client.researchConfig(value)
            flash(`SearXNG URL: ${result.url} · ${result.healthy ? "🟢" : "🟡"} ${result.detail}`)
          })()
          return true
        }
        if (first === "test") {
          const query = value || "PyTorch latest version"
          flash(`✦ Running test search for: ${query}…`)
          void (async () => {
            const result = await props.chat.client.researchTest(query)
            if (result.ok && result.results.length) {
              flash(
                result.results
                  .map(
                    (item, index) =>
                      `${index + 1}. ${item.title ?? item.url ?? "?"}${item.engine ? ` [${item.engine}]` : ""}`,
                  )
                  .join(" · "),
              )
            } else {
              flash(`No results for ${query}${result.error ? ` (${result.error})` : ""}`)
            }
          })()
          return true
        }
        flash("Usage: /websearch [on|off|status|test <query>|url <http://host:port>]")
        return true
      }
    }
  }

  const handleSend = (text: string) => {
    if (handleCommand(text)) return
    void props.chat.send(text)
  }

  const contentWidth = () =>
    Math.max(
      20,
      dims().width - (sidebarInline() ? SIDEBAR_WIDTH(dims().width) + LAYOUT.sidebar.gutter : 0),
    )

  /** Gemini-style pill: 70% of the main area, clamped to a sane range. */
  const pillWidth = () => {
    const main = contentWidth()
    return Math.min(main - 4, Math.max(46, Math.round(main * 0.7)))
  }

  const activeAssistantMessage = createMemo(() => {
    const sId = props.chat.streamingId()
    if (!sId) return undefined
    return props.chat.activeSession()?.messages.find((m) => m.id === sId)
  })

  const liveAgentState = createMemo(() =>
    deriveAgentState(
      streaming(),
      activeAssistantMessage(),
      props.chat.live(),
      metrics().tokPerSec,
    ),
  )

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
      onModel={openModelPicker}
      attachments={props.chat.attachments()}
      onAttach={(path) => void props.chat.addAttachment(path)}
      onRemoveAttachment={(id) => props.chat.removeAttachment(id)}
      onPicker={() => setOverlay({ kind: "picker" })}
      onPasteClipboard={() => props.chat.pasteClipboard()}
      suggestPaths={(query) => props.chat.client.suggestPaths(query).then((data) => data.paths)}
      phase={liveAgentState().phase}
      elapsed={liveAgentState().elapsed}
      tokensPerSecond={liveAgentState().tokensPerSecond}
      messageQueue={props.chat.messageQueue()}
      onRemoveQueueItem={(id) => props.chat.dequeueMessage(id)}
      onClearQueue={() => props.chat.clearQueue()}
      state={composerState}
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
        <Show when={sidebarInline()}>
          <Sidebar
            sessions={props.chat.sessions()}
            activeId={props.chat.activeId()}
            accent={accent()}
            streaming={streaming()}
            onNew={() => void props.chat.newChat()}
            onSelect={(id) => {
              setSidebarPicked(true)
              props.chat.select(id)
            }}
            onSettings={() => setOverlay({ kind: "settings" })}
          />
        </Show>
        <Show when={sidebarOverlay()}>
          <Sidebar
            overlay
            sessions={props.chat.sessions()}
            activeId={props.chat.activeId()}
            accent={accent()}
            streaming={streaming()}
            onNew={() => void props.chat.newChat()}
            onSelect={(id) => {
              setSidebarPicked(true)
              props.chat.select(id)
              closeSidebarOverlay()
            }}
            onSettings={() => setOverlay({ kind: "settings" })}
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
            dot={() => props.chat.connected()}
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
                <span style={{ fg: colors().faint }}> n new · s stop · l logs · v thinking · m models · t theme · a mode · h help · q quit</span>
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
                  contentWidth={contentWidth()}
                  onCopy={copyText}
                />
                <ComposerBlock placement="above" />
                <box height={1} flexShrink={0} />
                <StatusBar metrics={metrics()} width={contentWidth()} version={info()?.version} sessionsHint={!sidebar()} />
              </box>
            }
          >
            <box flexDirection="column" flexGrow={1} minHeight={0}>
              <Landing>
                <ComposerBlock placement="below" />
              </Landing>
              <box height={1} flexShrink={0} />
              <StatusBar metrics={metrics()} width={contentWidth()} version={info()?.version} sessionsHint={!sidebar()} />
            </box>
          </Show>
        </box>
      </box>

      <Show when={overlay()?.kind === "picker"}>
        <FilePickerOverlay
          accent={accent()}
          onPick={(path) => void props.chat.addAttachment(path)}
          onClose={() => setOverlay(null)}
        />
      </Show>
      <Show when={overlay()?.kind === "logs"}>
        <LogsOverlay client={props.chat.client} accent={accent()} onClose={() => setOverlay(null)} />
      </Show>
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
      <Show when={overlay()?.kind === "settings"}>
        <SettingsOverlay
          mode={info()?.mode ?? "chat"}
          accent={accent()}
          onPick={(row) => {
            if (row === "model") {
              setOverlay({ kind: "model" })
              return
            }
            if (row === "theme") {
              setOverlay({ kind: "theme" })
              return
            }
            if (row === "mode") {
              setOverlay(null)
              props.chat.toggleMode()
              return
            }
            setOverlay(null)
            void (async () => {
              try {
                const config = await props.chat.client.permissionConfig()
                const last = config.rules[config.rules.length - 1] ?? "none"
                flash(`Permission mode: ${config.mode} · ${config.rules.length} user rules · last: ${last}`)
              } catch (error) {
                flash(error instanceof Error ? error.message : String(error))
              }
            })()
          }}
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
