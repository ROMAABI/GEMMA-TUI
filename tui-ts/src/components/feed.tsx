import { For, createEffect, createMemo, on } from "solid-js"
import type { ScrollBoxRenderable } from "@opentui/core"
import type { ColorInput } from "@opentui/core"
import type { Chat } from "../chat"
import { LAYOUT } from "../layout"
import { colors } from "../theme"
import { MessageCard } from "./message"
import { deriveAgentState } from "./agent-state"

/** Scrollable transcript for the active session. */
export function Feed(props: {
  chat: Chat
  accent: ColorInput
  contentWidth: number
  onCopy: (text: string) => void
}) {
  let box: ScrollBoxRenderable | undefined
  const session = createMemo(() => props.chat.activeSession())
  const messages = createMemo(() => session()?.messages ?? [])
  const streamingId = createMemo(() => props.chat.streamingId())
  const isStreaming = createMemo(() => props.chat.streaming())

  // Dynamic live agent state
  const activeMessage = createMemo(() => {
    const sId = streamingId()
    if (!sId) return undefined
    return messages().find((m) => m.id === sId)
  })

  const liveState = createMemo(() =>
    deriveAgentState(
      isStreaming(),
      activeMessage(),
      props.chat.live(),
      props.chat.metrics().tokPerSec,
    ),
  )

  // Follow the tail while a reply streams in.
  createEffect(
    on(
      () => messages().map((message) => message.content.length).join(",") + streamingId(),
      () => {
        box?.scrollTo(Infinity)
      },
    ),
  )

  createEffect(
    on(
      () => props.chat.activeId(),
      () => box?.scrollTo(Infinity),
    ),
  )

  return (
    <box flexGrow={1} flexDirection="column" backgroundColor={colors().page} minHeight={0}>
      <scrollbox
        ref={box}
        flexGrow={1}
        stickyScroll
        stickyStart="bottom"
        verticalScrollbarOptions={{ visible: true }}
      >
        <box flexDirection="column" paddingLeft={1} paddingRight={1} paddingTop={1} paddingBottom={LAYOUT.composer.reserveRows}>
          <For each={messages()}>
            {(message) => (
              <MessageCard
                message={message}
                accent={props.accent}
                streaming={streamingId() === message.id}
                showThinking={props.chat.showThinking()}
                phase={streamingId() === message.id ? liveState().phase : undefined}
                elapsed={streamingId() === message.id ? liveState().elapsed : undefined}
                onCopy={props.onCopy}
              />
            )}
          </For>
        </box>
      </scrollbox>
    </box>
  )
}
