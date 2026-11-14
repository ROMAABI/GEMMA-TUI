import type { Metrics } from "../chat"
import { colors } from "../theme"
import { formatNumber } from "../format"

/**
 * Bottom strip: real context metrics on the left, command hint and version on
 * the right. Background is transparent so it inherits the canvas behind it.
 */
export function StatusBar(props: { metrics: Metrics; width: number; version?: string }) {
  const left = () => {
    const m = props.metrics
    const parts = [`ctx ${formatNumber(m.contextUsed)}/${formatNumber(m.contextMax)}`]
    if (m.tokPerSec > 0) parts.push(`${m.tokPerSec.toFixed(1)} tok/s`)
    return parts.join(" · ")
  }

  const right = () => {
    const items = ["ctrl+p commands"]
    if (props.version && props.width >= 70) items.push(`v${props.version}`)
    return items.join(" · ")
  }

  return (
    <box
      height={1}
      flexShrink={0}
      flexDirection="row"
      justifyContent="space-between"
      paddingLeft={2}
      paddingRight={2}
    >
      <text style={{ fg: colors().faint }} truncate>
        {left()}
      </text>
      <text style={{ fg: colors().faint }} truncate>
        {right()}
      </text>
    </box>
  )
}
