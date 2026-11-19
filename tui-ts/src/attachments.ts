/** Attachment helpers shared by the composer, picker and transcript. */

import { existsSync } from "node:fs"
import { basename } from "node:path"
import type { AttachmentDescriptor } from "./api"

/** Pull file paths out of a terminal paste (drag-and-drop / file:// URIs). */
export function extractPaths(text: string): string[] {
  const candidates: string[] = []
  const patterns = [
    /file:\/\/(?:localhost)?([^\s"']+)/gi,
    /"([^"\n]+\.[A-Za-z0-9]{1,6})"/g,
    /'([^'\n]+\.[A-Za-z0-9]{1,6})'/g,
    /(?:^|[\s(])((?:~|\/)[^\s"'`()]+)/g,
  ]
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const raw = match[1] ?? ""
      try {
        const decoded = decodeURIComponent(raw)
        if (existsSync(decoded.replace(/^file:\/\//, ""))) candidates.push(decoded)
      } catch {
        // Ignore malformed URI escapes.
      }
    }
  }
  return [...new Set(candidates)]
}

export function formatBytes(bytes = 0): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

export function formatDuration(seconds = 0): string {
  const total = Math.max(0, Math.round(seconds))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`
}

/** Kitty's graphics protocol is advertised through these env vars. */
export function kittySupported(): boolean {
  return Boolean(
    process.env.KITTY_WINDOW_ID ||
      process.env.TERM?.toLowerCase().includes("kitty") ||
      process.env.TERM_PROGRAM?.toLowerCase() === "kitty",
  )
}

/** Colorless chafa art, safe to print as normal terminal text. */
export function chafaLines(path: string, cols = 18, rows = 8): string[] {
  try {
    const proc = Bun.spawnSync(
      ["chafa", "--format", "symbols", "--colors", "none", "--size", `${cols}x${rows}`, path],
      { stdout: "pipe", stderr: "pipe" },
    )
    if (proc.exitCode !== 0) return []
    return new TextDecoder()
      .decode(proc.stdout)
      .replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "")
      .split("\n")
      .map((line) => line.trimEnd())
      .filter(Boolean)
      .slice(0, rows)
  } catch {
    return []
  }
}

export function attachmentIcon(kind: AttachmentDescriptor["kind"]): string {
  if (kind === "image") return "🖼"
  if (kind === "video") return "🎞"
  return "📄"
}

export function attachmentLabel(attachment: AttachmentDescriptor): string {
  const head = `${attachmentIcon(attachment.kind)} ${basename(attachment.name)}`
  if (attachment.kind === "video") {
    return `${head} ${formatDuration(attachment.duration)} · ${attachment.frames ?? 0} frames`
  }
  if (attachment.kind === "image") return `${head} ${formatBytes(attachment.size)}`
  return `${head} ${attachment.kind.toUpperCase()}`
}

/** Store descriptors without generated image data (only paths and text). */
export function DBDescriptor(attachment: AttachmentDescriptor): AttachmentDescriptor {
  const { ...clean } = attachment as AttachmentDescriptor & Record<string, unknown>
  delete (clean as Record<string, unknown>).image_data
  delete (clean as Record<string, unknown>).image_data_list
  return clean
}
