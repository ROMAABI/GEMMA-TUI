import { For, Show, createMemo } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import type { Session } from "../chat"
import { colors, theme, tint, type ResolvedTheme } from "../theme"
import { relativeTime, truncate, username } from "../format"
import { LAYOUT, SIDEBAR_WIDTH, sessionTitleWidth } from "../layout"

/** Gemini-style left rail: brand + search, navigation, the session list and a
 * user footer. The rail is always a step darker than the page behind it.
 */
export function Sidebar(props: {
  sessions: Session[]
  activeId: string
  accent: ResolvedTheme
  streaming: boolean
  onNew: () => void
  onSelect: (id: string) => void
  onSettings: () => void
}) {
  const dims = useTerminalDimensions()
  const width = () => SIDEBAR_WIDTH(dims().width)
  const titleBudget = createMemo(() => sessionTitleWidth(dims().width))
  const listHeight = createMemo(() =>
    Math.max(LAYOUT.sidebar.minListRows, dims().height - LAYOUT.sidebar.chromeRows),
  )
  const rail = () => {
    const page = colors().page
    // System themes may hand us an RGBA object; only hex strings can be tinted.
    if (typeof page !== "string") return page
    return tint(page, "#000000", theme().mode === "dark" ? 0.5 : 0.05)
  }

  return (
    <box
      width={width()}
      flexShrink={0}
      flexDirection="column"
      backgroundColor={rail()}
      paddingLeft={1}
      paddingRight={1}
      paddingTop={1}
    >
      <box flexDirection="row" height={1} flexShrink={0} justifyContent="space-between">
        <text>
          <b style={{ fg: props.accent.accent }}>◆ gemma</b>
        </text>
        <text style={{ fg: colors().muted }}>⌕</text>
      </box>

      <box flexDirection="column" flexShrink={0} marginTop={1}>
        <box
          flexDirection="row"
          height={1}
          backgroundColor={colors().element}
          paddingLeft={1}
          paddingRight={1}
          onMouseDown={props.onNew}
        >
          <text style={{ fg: colors().text }} flexGrow={1} truncate>
            <b>New chat</b>
          </text>
          <text style={{ fg: colors().faint }}>ctrl+n</text>
        </box>
      </box>

      <box flexDirection="row" height={1} flexShrink={0} marginTop={1} justifyContent="space-between">
        <text style={{ fg: colors().muted }}>
          <b>Sessions</b>
        </text>
        <text style={{ fg: colors().faint }}>r rename</text>
      </box>
      <scrollbox
        height={listHeight()}
        flexShrink={0}
        marginTop={1}
        verticalScrollbarOptions={{ visible: false }}
      >
        <Show
          when={props.sessions.length > 0}
          fallback={<text style={{ fg: colors().faint }}>No sessions yet</text>}
        >
          <For each={props.sessions}>
            {(session) => {
              const active = () => session.id === props.activeId
              return (
                <box
                  flexDirection="row"
                  height={1}
                  backgroundColor={active() ? colors().element : rail()}
                  paddingLeft={1}
                  paddingRight={1}
                  onMouseDown={() => props.onSelect(session.id)}
                >
                  <text
                    style={{ fg: active() ? colors().text : colors().dim }}
                    flexGrow={1}
                    truncate
                  >
                    {truncate(session.title || "New chat", titleBudget())}
                  </text>
                  <text style={{ fg: colors().faint }}>{relativeTime(session.updatedAt)}</text>
                </box>
              )
            }}
          </For>
        </Show>
      </scrollbox>

      <box flexDirection="column" flexShrink={0} marginTop={1} paddingLeft={1}>
        <box flexDirection="row" height={1} gap={1}>
          <text>
            <b style={{ fg: colors().text }}>{username()}</b>
          </text>
          <Show when={props.streaming}>
            <text style={{ fg: colors().warning }}>●</text>
          </Show>
        </box>
        <box flexDirection="row" height={1} justifyContent="space-between">
          <text style={{ fg: colors().faint }}>local · offline</text>
          <text style={{ fg: colors().muted }} onMouseDown={props.onSettings}>
            ⚙
          </text>
        </box>
      </box>
    </box>
  )
}
