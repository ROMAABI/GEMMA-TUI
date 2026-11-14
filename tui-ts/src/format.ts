/** Small formatting helpers shared by the components. */

export function clockTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
}

export function relativeTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000))
  if (seconds < 60) return "now"
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d`
  return date.toLocaleDateString([], { month: "short", day: "numeric" })
}

/** Rough token estimate (~4 chars per token), good enough for the metrics row. */
export function estimateTokens(text: string): number {
  if (!text) return 0
  return Math.max(1, Math.round(text.length / 4))
}

export function titleFromPrompt(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim()
  return clean.length > 48 ? `${clean.slice(0, 47)}…` : clean || "New chat"
}

export function truncate(text: string, width: number): string {
  if (width <= 1) return ""
  if (text.length <= width) return text
  return `${text.slice(0, Math.max(0, width - 1))}…`
}

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US")
}

/** Local account name, capitalized — the same label the classic TUI shows. */
export function username(): string {
  const raw = process.env.USER || process.env.LOGNAME || "you"
  return raw.charAt(0).toUpperCase() + raw.slice(1)
}

export function formatSeconds(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0.0s"
  if (value < 10) return `${value.toFixed(1)}s`
  if (value < 60) return `${Math.round(value)}s`
  const minutes = Math.floor(value / 60)
  return `${minutes}m${Math.round(value % 60)}s`
}
