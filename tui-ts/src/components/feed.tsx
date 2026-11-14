import { For, createEffect, createMemo, on } from "solid-js"
import type { ScrollBoxRenderable } from "@opentui/core"
import type { ColorInput } from "@opentui/core"
import type { Chat } from "../chat"
import { colors } from "../theme"
import { MessageCard } from "./message"

/** Scrollable transcript for the active session. */
export function Feed(props: { chat: Chat; accent: ColorInput; model: string; onCopy: (text: string) => void }) {
  let box: ScrollBoxRenderable | undefined
  const session = createMemo(() => props.chat.activeSession())
  const messages = createMemo(() => session()?.messages ?? [])
  const streamingId = createMemo(() => props.chat.streamingId())

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
        <box flexDirection="column" paddingLeft={1} paddingRight={1} paddingTop={1}>
          <For each={messages()}>
            {(message) => (
              <MessageCard
                message={message}
                accent={props.accent}
                model={props.model}
                streaming={streamingId() === message.id}
                onCopy={props.onCopy}
              />
            )}
          </For>
        </box>
      </scrollbox>
    </box>
  )
}
