import { For, Match, Show, Switch, createMemo, type Accessor } from "solid-js"
import type { ColorInput } from "@opentui/core"
import { inline, parseMarkdown, type Block, type Segment } from "../markdown"
import { colors } from "../theme"
import { useRenderer } from "@opentui/solid"
import { SyntaxStyle } from "@opentui/core"

function InlineSegments(props: { text: string; accent: ColorInput; base?: ColorInput }) {
  const segments = createMemo(() => inline(props.text))
  return (
    <For each={segments()}>
      {(segment: Segment) => (
        <Switch>
          <Match when={segment.bold}>
            <b style={{ fg: props.base ?? colors().text }}>{segment.text}</b>
          </Match>
          <Match when={segment.italic}>
            <i style={{ fg: props.base ?? colors().dim }}>{segment.text}</i>
          </Match>
          <Match when={segment.code}>
            <span style={{ fg: props.accent, bg: colors().code }}>
              {` ${segment.text} `}
            </span>
          </Match>
          <Match when={segment.link}>
            <span style={{ fg: props.accent }}>{segment.text}</span>
          </Match>
          <Match when={true}>
            <span style={{ fg: props.base ?? colors().text }}>{segment.text}</span>
          </Match>
        </Switch>
      )}
    </For>
  )
}

function CodeBlock(props: { lang: string; text: string; accent: ColorInput; onCopy?: (text: string) => void }) {
  const renderer = useRenderer()
  const style = SyntaxStyle.create(renderer.nativeScene)
  return (
    <box
      flexDirection="column"
      border
      borderStyle="rounded"
      borderColor={colors().border}
      backgroundColor={colors().code}
      marginTop={1}
      marginBottom={1}
      flexShrink={0}
    >
      <box
        flexDirection="row"
        justifyContent="space-between"
        height={1}
        backgroundColor={colors().card}
        paddingLeft={1}
        paddingRight={1}
      >
        <text style={{ fg: props.accent }}>
          <b>{props.lang || "code"}</b>
        </text>
        <text style={{ fg: colors().faint }} onMouseDown={() => props.onCopy?.(props.text)}>
          ❐ Copy
        </text>
      </box>
      <box paddingLeft={1} paddingRight={1} paddingTop={1} paddingBottom={1}>
        <code content={props.text} filetype={props.lang || "text"} syntaxStyle={style} />
      </box>
    </box>
  )
}

function BlockView(props: { block: Block; accent: ColorInput; onCopy?: (text: string) => void }) {
  return (
    <Switch>
      <Match when={props.block.kind === "code" ? props.block : null}>
        {(block: Accessor<{ kind: "code"; lang: string; text: string }>) => (
          <CodeBlock
            lang={block().lang}
            text={block().text}
            accent={props.accent}
            onCopy={props.onCopy}
          />
        )}
      </Match>

      <Match when={props.block.kind === "heading" ? props.block : null}>
        {(block: Accessor<{ kind: "heading"; level: number; text: string }>) => (
          <text style={{ fg: block().level <= 2 ? props.accent : colors().text }} wrapMode="word" marginTop={1}>
            <b>{block().text}</b>
          </text>
        )}
      </Match>

      <Match when={props.block.kind === "list" ? props.block : null}>
        {(block: Accessor<{ kind: "list"; ordered: boolean; items: string[] }>) => (
          <box flexDirection="column">
            <For each={block().items}>
              {(item, index) => (
                <text wrapMode="word">
                  <span style={{ fg: props.accent }}>{block().ordered ? `${index() + 1}.` : "•"}</span>{" "}
                  <InlineSegments text={item} accent={props.accent} />
                </text>
              )}
            </For>
          </box>
        )}
      </Match>

      <Match when={props.block.kind === "quote" ? props.block : null}>
        {(block: Accessor<{ kind: "quote"; text: string }>) => (
          <box border={["left"]} borderColor={colors().border} paddingLeft={1}>
            <text style={{ fg: colors().dim }} wrapMode="word">{block().text}</text>
          </box>
        )}
      </Match>

      <Match when={props.block.kind === "table" ? props.block : null}>
        {(block: Accessor<{ kind: "table"; text: string }>) => (
          <CodeBlock lang="text" text={block().text} accent={props.accent} onCopy={props.onCopy} />
        )}
      </Match>

      <Match when={props.block.kind === "hr"}>
        <text style={{ fg: colors().border }}>{"─".repeat(24)}</text>
      </Match>

      <Match when={props.block.kind === "paragraph" ? props.block : null}>
        {(block: Accessor<{ kind: "paragraph"; text: string }>) => (
          <text wrapMode="word">
            <InlineSegments text={block().text} accent={props.accent} />
          </text>
        )}
      </Match>
    </Switch>
  )
}

/** Renders assistant markdown as OpenTUI text and code cards. */
export function MarkdownView(props: {
  content: string
  accent: ColorInput
  onCopy?: (text: string) => void
}) {
  const blocks = createMemo(() => parseMarkdown(props.content))
  return (
    <box flexDirection="column">
      <For each={blocks()}>
        {(block) => <BlockView block={block} accent={props.accent} onCopy={props.onCopy} />}
      </For>
    </box>
  )
}

/** Renders a user message: plain text, no markdown. */
export function PlainView(props: { content: string }) {
  return (
    <box flexDirection="column">
      <For each={props.content.split("\n")}>
        {(line) => <text style={{ fg: colors().text }}>{line || " "}</text>}
      </For>
    </box>
  )
}

export function CodeNote(props: { text: string }) {
  return (
    <Show when={props.text.trim()}>
      <text style={{ fg: colors().dim }}>{props.text}</text>
    </Show>
  )
}
