import { For, Match, Show, Switch, createMemo, createSignal } from "solid-js"
import type { ColorInput } from "@opentui/core"
import type { AttachmentDescriptor } from "../api"
import {
  attachmentIcon, attachmentLabel, chafaLines, kittySupported,
} from "../attachments"
import type { Message, Part } from "../chat"
import { colors } from "../theme"
import { clockTime, formatNumber, formatSeconds } from "../format"
import { MarkdownView, PlainView } from "./markdown"
import { PacManHeaderSpinner, type AgentPhase } from "./pacman"

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
    if (!text) return ""
    const firstLine = text.split("\n")[0] ?? ""
    return firstLine.length > 56 ? `${firstLine.slice(0, 55)}…` : firstLine
  })
  const summary = createMemo(() => summarizeArgs(props.part.args))

  return (
    <box
      flexDirection="column"
      flexShrink={0}
      marginTop={1}
      border={["left"]}
      borderColor={color()}
      paddingLeft={1}
    >
      <box flexDirection="row" height={1} gap={1}>
        <text style={{ fg: color() }}>
          <b>{icon()}</b>
        </text>
        <text style={{ fg: colors().text }}>
          <b>{props.part.name}</b>
        </text>
        <Show when={summary()}>
          <text style={{ fg: colors().dim }} truncate>
            {summary()}
          </text>
        </Show>
        <box flexGrow={1} />
        <Show when={duration()}>
          <text style={{ fg: colors().faint }}>{duration()}</text>
        </Show>
      </box>
      <Show when={preview() && props.part.status !== "running"}>
        <text
          style={{ fg: props.part.status === "error" ? colors().danger : colors().muted }}
          truncate
        >
          {preview()}
        </text>
      </Show>
    </box>
  )
}

function ReasoningBlock(props: { text: string; streaming: boolean; expandAll: boolean }) {
  const [expanded, setExpanded] = createSignal(false)
  const open = () => expanded() || props.expandAll
  const charCount = createMemo(() => Array.from(props.text.trim()).length)
  return (
    <box
      flexDirection="column"
      flexShrink={0}
      marginTop={1}
      border={["left"]}
      borderColor={colors().border}
      paddingLeft={1}
    >
      <text
        style={{ fg: colors().faint }}
        height={1}
        onMouseDown={() => setExpanded(!expanded())}
      >
        {open() ? "▾ thinking" : "▸ thinking…"}
        {props.streaming ? " (working)" : ""}
        {!props.streaming && charCount() > 0 ? ` · ${formatNumber(charCount())} chars · click or ^X v` : ""}
      </text>
      <Show when={open() && props.text.trim()}>
        <text style={{ fg: colors().muted }} wrapMode="word">
          <i>{props.text.trim()}</i>
        </text>
      </Show>
    </box>
  )
}

function SourcesBlock(props: { sources?: Array<{ title: string; domain: string; date?: string }> }) {
  return (
    <box flexDirection="column" flexShrink={0} marginTop={1} border={["left"]} borderColor={colors().border} paddingLeft={1}>
      <text style={{ fg: colors().faint }} height={1}>
        {`sources · ${props.sources?.length ?? 0}`}
      </text>
      <For each={props.sources ?? []}>
        {(source, index) => (
          <text style={{ fg: colors().muted }} wrapMode="word">
            {`${index() + 1}. ${source.title || source.domain} — ${source.domain}${
              source.date ? ` · ${source.date}` : ""
            }`}
          </text>
        )}
      </For>
    </box>
  )
}

/** Attachment chip with a small inline preview when the terminal can show one. */
function AttachmentCard(props: { attachment: AttachmentDescriptor; accent: ColorInput }) {
  const art = createMemo(() =>
    props.attachment.preview && !kittySupported() ? chafaLines(props.attachment.preview) : [],
  )
  return (
    <box
      flexDirection="column"
      marginTop={1}
      backgroundColor={colors().panel}
      border
      borderStyle="rounded"
      borderColor={colors().border}
      paddingLeft={1}
      paddingRight={1}
      flexShrink={0}
    >
      <box flexDirection="row" height={1} gap={1}>
        <text style={{ fg: props.accent }}>{attachmentIcon(props.attachment.kind)}</text>
        <text style={{ fg: colors().text }} flexGrow={1} truncate>
          {attachmentLabel(props.attachment)}
        </text>
        <text style={{ fg: colors().faint }}>{props.attachment.kind}</text>
      </box>
      <Show when={art().length > 0}>
        <box flexDirection="column" marginTop={1}>
          <For each={art()}>
            {(line) => (
              <text flexShrink={0}>
                {line}
              </text>
            )}
          </For>
        </box>
      </Show>
    </box>
  )
}

/** Web-search status for the turn: running line, failure line, or sources. */
function SearchBlock(props: { search: NonNullable<Message["search"]>; accent: ColorInput }) {
  return (
    <Switch>
      <Match when={props.search.status === "searching"}>
        <box flexDirection="row" height={1} gap={1} marginTop={1}>
          <text style={{ fg: colors().warning }}>▸</text>
          <text style={{ fg: colors().text }}>
            <b>search</b>
          </text>
          <text style={{ fg: colors().dim }}>searching the web…</text>
        </box>
      </Match>
      <Match when={props.search.status === "reading"}>
        <box flexDirection="row" height={1} gap={1} marginTop={1}>
          <text style={{ fg: colors().warning }}>▸</text>
          <text style={{ fg: colors().text }}>
            <b>read</b>
          </text>
          <text style={{ fg: colors().dim }}>
            {`reading ${props.search.count ?? 0} pages…`}
          </text>
        </box>
      </Match>
      <Match when={props.search.status === "failed"}>
        <box flexDirection="row" height={1} gap={1} marginTop={1}>
          <text style={{ fg: colors().danger }}>✗</text>
          <text style={{ fg: colors().danger }}>
            {props.search.error ?? "search failed"}
          </text>
        </box>
      </Match>
      <Match when={props.search.status === "ok"}>
        <SourcesBlock sources={props.search.sources} />
      </Match>
    </Switch>
  )
}

/** One transcript entry, OpenCode style: user blocks are tinted, replies are plain. */
export function MessageCard(props: {
  message: Message
  accent: ColorInput
  streaming: boolean
  showThinking: boolean
  phase?: AgentPhase
  elapsed?: number
  onCopy: (text: string) => void
}) {
  const isUser = () => props.message.role === "user"

  // Derive active phase dynamically from message parts and search if not explicitly passed
  const currentPhase = createMemo<AgentPhase>(() => {
    if (props.phase) return props.phase
    if (props.message.search?.status === "searching") return "searching"
    if (props.message.search?.status === "reading") return "reading"
    const runningTool = props.message.parts.find(
      (p): p is Extract<Part, { kind: "tool" }> => p.kind === "tool" && p.status === "running",
    )
    if (runningTool) {
      const name = runningTool.name.toLowerCase()
      if (name.includes("bash") || name.includes("command") || name.includes("exec")) return "bash"
      if (name.includes("search") || name.includes("find") || name.includes("grep")) return "searching"
      if (name.includes("read") || name.includes("view") || name.includes("fetch")) return "reading"
      return "bash"
    }
    const lastPart = props.message.parts[props.message.parts.length - 1]
    if (lastPart?.kind === "reasoning") return "thinking"
    if (lastPart?.kind === "text") return "writing"
    return "thinking"
  })

  return (
    <box flexDirection="column" width="100%" flexShrink={0} marginBottom={1}>
      <Show
        when={isUser()}
        fallback={
          <box flexDirection="column" width="100%" paddingLeft={1} paddingRight={1}>
            <box flexDirection="row" height={1} gap={1} alignItems="center">
              <text style={{ fg: props.accent }}>
                <b>gemma</b>
              </text>
              <text style={{ fg: colors().faint }}>{clockTime(props.message.createdAt)}</text>
              <Show when={props.streaming}>
                <PacManHeaderSpinner
                  phase={currentPhase()}
                  elapsed={props.elapsed ?? props.message.elapsed ?? 0}
                />
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
                    <ReasoningBlock
                      text={part.kind === "reasoning" ? part.text : ""}
                      streaming={props.streaming}
                      expandAll={props.showThinking}
                    />
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
              <Show when={typeof props.message.elapsed === "number" && (!props.streaming || props.message.error || props.message.stopped)}>
                {(() => {
                  const genSeconds = (props.message.genMs ?? 0) / 1000
                  const tokens = props.message.tokens ?? props.message.content.length / 4
                  const rate = genSeconds > 0
                    ? Math.round(tokens / genSeconds)
                    : Math.round(tokens / Math.max((props.message.elapsed ?? 0) - (props.message.ttft ?? 0), 0.001))
                  const toolMs = props.message.parts.reduce(
                    (sum, part) =>
                      part.kind === "tool" && part.finishedAt && part.startedAt
                        ? sum + (part.finishedAt - part.startedAt)
                        : sum,
                    0,
                  )
  const toolTime = toolMs < 1000 ? `${Math.round(toolMs)}ms` : formatSeconds(toolMs / 1000)
                  return (
                    <text style={{ fg: colors().faint }}>
                      {`⚡ ${formatSeconds(props.message.elapsed ?? 0)}`}
                      {` · ${formatNumber(rate)} tok/s`}
                      {toolMs > 0 ? ` · tools ${toolTime}` : ""}
                    </text>
                  )
                })()}
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
          <For each={props.message.attachments ?? []}>
            {(attachment) => (
              <AttachmentCard attachment={attachment} accent={props.accent} />
            )}
          </For>
        </box>
      </Show>
    </box>
  )
}
