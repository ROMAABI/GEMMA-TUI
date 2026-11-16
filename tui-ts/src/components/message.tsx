import { For, Match, Show, Switch, createMemo } from "solid-js"
import type { ColorInput } from "@opentui/core"
import type { Message, Part } from "../chat"
import { colors } from "../theme"
import { clockTime, formatNumber, formatSeconds } from "../format"
import { MarkdownView, PlainView } from "./markdown"

function summarizeArgs(args: Record<string, unknown>): string {
  const preferred = ["command", "path", "file_path", "pattern", "query", "url", "name", "prompt"]
  for (const key of preferred) {
    const value = args[key]
    if (typeof value === "string" && value.trim()) {
      const clean = value.replace(/\s+/g, " ").trim()
      return clean.length > 56 ? `${clean.slice(0, 55)}…` : clean
    }
  }
  const first = Object.values(args).find((value) => typeof value === "string")
  return typeof first === "string" ? first.slice(0, 56) : ""
}

function ToolBlock(props: { part: Extract<Part, { kind: "tool" }>; accent: ColorInput }) {
  const icon = () =>
    props.part.status === "running" ? "▸" : props.part.status === "error" ? "✗" : "✓"
  const color = () =>
    props.part.status === "running"
      ? colors().warning
      : props.part.status === "error"
        ? colors().danger
        : colors().success
  const duration = () =>
    props.part.finishedAt && props.part.startedAt
      ? formatSeconds((props.part.finishedAt - props.part.startedAt) / 1000)
      : ""
  const preview = createMemo(() => {
    const text = (props.part.result ?? "").trim()
    if (!text) return { lines: [] as string[], hidden: 0 }
    const lines = text.split("\n")
    const shown = lines.slice(0, 15)
    return { lines: shown, hidden: Math.max(lines.length - shown.length, 0) }
  })

  return (
    <box flexDirection="column" flexShrink={0} marginTop={1}>
      <box flexDirection="row" height={1} gap={1}>
        <text style={{ fg: color() }}>{icon()}</text>
        <text style={{ fg: colors().text }}>
          <b>{props.part.name}</b>
        </text>
        <text style={{ fg: colors().faint }} truncate>
          {summarizeArgs(props.part.args)}
        </text>
        <Show when={duration()}>
          <text style={{ fg: colors().faint }}>{duration()}</text>
        </Show>
      </box>
      <Show when={preview().lines.length}>
        <box
          border={["left"]}
          borderColor={colors().border}
          paddingLeft={1}
          marginLeft={1}
          flexDirection="column"
          flexShrink={0}
        >
          <For each={preview().lines}>
            {(line) => (
              <text
                style={{ fg: props.part.status === "error" ? colors().danger : colors().muted }}
                wrapMode="word"
              >
                {line || " "}
              </text>
            )}
          </For>
          <Show when={preview().hidden > 0}>
            <text style={{ fg: colors().faint }}>{`… ${preview().hidden} more lines`}</text>
          </Show>
        </box>
      </Show>
    </box>
  )
}

function ReasoningBlock(props: { text: string }) {
  const preview = createMemo(() => {
    const lines = props.text.trim().split("\n")
    return lines.length > 6 ? `${lines.slice(0, 6).join("\n")}…` : lines.join("\n")
  })
  return (
    <box flexDirection="column" flexShrink={0} marginTop={1} border={["left"]} borderColor={colors().border} paddingLeft={1}>
      <text style={{ fg: colors().faint }} height={1}>
        <i>thinking</i>
      </text>
      <text style={{ fg: colors().muted }} wrapMode="word">
        <i>{preview()}</i>
      </text>
    </box>
  )
}

/** Web-search status for the turn: running line, failure line, or sources. */
function SearchBlock(props: { search: NonNullable<Message["search"]>; accent: ColorInput }) {
  return (
    <Switch>
      <Match when={props.search.status === "searching"}>
        <box flexDirection="row" height={1} gap={1} marginTop={1}>
          <text style={{ fg: props.accent }}>✦</text>
          <text style={{ fg: colors().muted }}>Searching the web…</text>
        </box>
      </Match>
      <Match when={props.search.status === "reading"}>
        <box flexDirection="row" height={1} gap={1} marginTop={1}>
          <text style={{ fg: props.accent }}>✦</text>
          <text style={{ fg: colors().muted }}>Reading top results…</text>
        </box>
      </Match>
      <Match when={props.search.status === "failed"}>
        <box flexDirection="row" gap={1} marginTop={1} flexShrink={0}>
          <text style={{ fg: colors().danger }}>⚠</text>
          <text style={{ fg: colors().danger }} wrapMode="word" flexGrow={1}>
            {`Web search failed: ${props.search.error ?? "unavailable"} — answering from model memory`}
          </text>
        </box>
      </Match>
      <Match when={props.search.status === "ok"}>
        <box flexDirection="column" flexShrink={0} marginTop={1} border={["left"]} borderColor={colors().border} paddingLeft={1}>
          <text style={{ fg: colors().faint }} height={1}>
            {`sources · ${props.search.sources?.length ?? 0}`}
          </text>
          <For each={props.search.sources ?? []}>
            {(source, index) => (
              <text style={{ fg: colors().muted }} wrapMode="word">
                {`${index() + 1}. ${source.title || source.domain} — ${source.domain}${
                  source.date ? ` · ${source.date}` : ""
                }`}
              </text>
            )}
          </For>
        </box>
      </Match>
    </Switch>
  )
}

/** One transcript entry, OpenCode style: user blocks are tinted, replies are plain. */
export function MessageCard(props: {
  message: Message
  accent: ColorInput
  streaming: boolean
  onCopy: (text: string) => void
}) {
  const isUser = () => props.message.role === "user"

  return (
    <box flexDirection="column" width="100%" flexShrink={0} marginBottom={1}>
      <Show
        when={isUser()}
        fallback={
          <box flexDirection="column" width="100%" paddingLeft={1} paddingRight={1}>
            <box flexDirection="row" height={1} gap={1}>
              <text style={{ fg: props.accent }}>
                <b>gemma</b>
              </text>
              <text style={{ fg: colors().faint }}>{clockTime(props.message.createdAt)}</text>
              <Show when={props.streaming}>
                <text style={{ fg: colors().warning }}>● working</text>
              </Show>
              <Show when={props.message.stopped}>
                <text style={{ fg: colors().warning }}>stopped</text>
              </Show>
            </box>

            <For each={props.message.parts}>
              {(part) => (
                <Switch>
                  <Match when={part.kind === "tool"}>
                    <ToolBlock part={part as Extract<Part, { kind: "tool" }>} accent={props.accent} />
                  </Match>
                  <Match when={part.kind === "reasoning"}>
                    <ReasoningBlock text={part.kind === "reasoning" ? part.text : ""} />
                  </Match>
                  <Match when={part.kind === "text"}>
                    <box flexDirection="column" marginTop={1}>
                      <MarkdownView
                        content={part.kind === "text" ? part.text : ""}
                        accent={props.accent}
                        onCopy={props.onCopy}
                      />
                    </box>
                  </Match>
                </Switch>
              )}
            </For>

            <Show when={props.message.search}>
              <SearchBlock search={props.message.search!} accent={props.accent} />
            </Show>

            <Show when={props.message.error}>
              <text style={{ fg: colors().danger }} marginTop={1}>
                {props.message.error}
              </text>
            </Show>

            <Show when={!props.message.parts.length && props.streaming}>
              <text style={{ fg: colors().faint }} marginTop={1}>
                …
              </text>
            </Show>

            <box flexDirection="row" height={1} gap={1} marginTop={1}>
              <Show when={typeof props.message.elapsed === "number" && !props.streaming}>
                <text style={{ fg: colors().faint }}>
                  {`⚡ ${formatSeconds(props.message.elapsed ?? 0)}`}
                  {` · ${formatNumber(
                    Math.round(
                      (props.message.tokens ?? props.message.content.length / 4) /
                        Math.max((props.message.elapsed ?? 0) - (props.message.ttft ?? 0), 0.001),
                    ),
                  )} tok/s`}
                </text>
              </Show>
              <box flexGrow={1} />
              <text style={{ fg: colors().faint }} onMouseDown={() => props.onCopy(props.message.content)}>
                ❐ copy
              </text>
            </box>
          </box>
        }
      >
        <box
          flexDirection="column"
          width="100%"
          backgroundColor={colors().card}
          border={["left"]}
          borderColor={props.accent}
          paddingLeft={1}
          paddingRight={1}
        >
          <box flexDirection="row" height={1} gap={1}>
            <text style={{ fg: props.accent }}>
              <b>you</b>
            </text>
            <text style={{ fg: colors().faint }}>{clockTime(props.message.createdAt)}</text>
          </box>
          <PlainView content={props.message.content} />
        </box>
      </Show>
    </box>
  )
}
