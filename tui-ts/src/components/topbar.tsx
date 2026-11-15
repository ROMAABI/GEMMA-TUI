import { createSignal, onCleanup } from "solid-js"
import { colors } from "../theme"
import { truncate } from "../format"

function clockLabel(): string {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
}

/** Slim top bar: session title on the left, connection dot, model and clock on the right. */
export function Header(props: {
  title: string
  model: string
  width: number
  dot: () => boolean | null
}) {
  const [clock, setClock] = createSignal(clockLabel())
  const timer = setInterval(() => setClock(clockLabel()), 10_000)
  onCleanup(() => clearInterval(timer))

  const compact = () => props.width < 60
  const titleBudget = () => Math.max(10, props.width - (compact() ? 18 : 36))
  const dotColor = () => {
    const state = props.dot()
    if (state === true) return colors().success
    if (state === false) return colors().danger
    return colors().faint
  }

  return (
    <box
      height={1}
      flexShrink={0}
      flexDirection="row"
      alignItems="center"
      paddingLeft={1}
      paddingRight={2}
    >
      <text style={{ fg: colors().text }} flexGrow={1} truncate>
        <b>{truncate(props.title || "New session", titleBudget())}</b>
      </text>
      <text style={{ fg: dotColor() }} marginRight={1}>
        ●
      </text>
      <text style={{ fg: colors().muted }} marginRight={1}>
        {truncate(props.model || "no model", compact() ? 10 : 22)}
      </text>
      {!compact() && <text style={{ fg: colors().faint }}>{clock()}</text>}
    </box>
  )
}
