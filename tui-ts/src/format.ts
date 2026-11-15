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

/** File formats and fine-tune noise that never belong in a display name. */
const MODEL_NOISE = new Set([
  "gguf",
  "ggml",
  "safetensors",
  "mmproj",
  "awq",
  "gptq",
  "exl2",
  "exl3",
  "nf4",
  "bf16",
  "f16",
  "f32",
  "fp16",
  "fp32",
  "int8",
  "i8",
  "it",
  "hf",
  "uncensored",
  "heretic",
  "abliterated",
  "nsfw",
])

/**
 * Short, clean name for a model id. The full id still goes to the API — this
 * only changes what the user sees.
 *
 *   "gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M" -> "gemma 4"
 *   "llama-3.1-8b-instruct-Q4_K_M.gguf"              -> "llama 3 1 8b instruct"
 */
export function displayModelName(raw: string): string {
  const trimmed = (raw ?? "").trim()
  if (!trimmed) return ""
  // Drop directories and the file extension.
  const base = trimmed.split(/[\\/]/).pop() ?? trimmed
  const stem = base.replace(/\.(gguf|bin|safetensors|pt|pth)$/i, "")
  // Gemma keeps one canonical name, with the size when there is one.
  const gemma = /^gemma[-_ ]?(\d+)(?:[-_ ](\d+(?:\.\d+)?b))?/i.exec(stem)
  if (gemma) {
    return gemma[2] ? `gemma ${gemma[1]} ${gemma[2]}` : `gemma ${gemma[1]}`
  }
  // Strip quantization markers (Q4_K_M, IQ4_XS, BF16, …) and the noise words.
  const withoutQuant = stem.replace(
    /[._-]?(i?q\d+(?:_[a-z0-9]+)*|iq\d+_[a-z0-9_]*|bf16|fp16|fp32|f16|f32|int8|i8)/gi,
    " ",
  )
  const words = withoutQuant
    .split(/[-_\s]+/)
    .map((word) => word.trim())
    .filter((word) => word.length > 0 && !MODEL_NOISE.has(word.toLowerCase()))
  const name = words.join(" ")
  if (!name) {
    const fallback = withoutQuant.replace(/[-_\s]+/g, " ").trim()
    return fallback.length > 28 ? `${fallback.slice(0, 27)}…` : fallback
  }
  return name.length > 28 ? `${name.slice(0, 27)}…` : name
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
