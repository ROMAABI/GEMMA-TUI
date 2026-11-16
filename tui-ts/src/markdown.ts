/**
 * Small markdown reader for assistant replies.
 *
 * OpenTUI's `<markdown>` renderable depends on tree-sitter highlighting and
 * paints nothing without it, so we parse the common constructs ourselves and
 * render plain styled text plus `<code>` blocks.
 */

export interface Segment {
  text: string
  bold?: boolean
  italic?: boolean
  code?: boolean
  link?: boolean
}

export type Block =
  | { kind: "code"; lang: string; text: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; start: number; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "table"; text: string }
  | { kind: "hr" }
  | { kind: "paragraph"; text: string }

const FENCE = /^\s*(```|~~~)\s*([\w+-]*)\s*$/
const HEADING = /^(#{1,6})\s+(.*)$/
const BULLET = /^\s*[-*+]\s+(.*)$/
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/
const RULE = /^\s*([-_*])\1{2,}\s*$/

/** Split assistant markdown into renderable blocks. */
export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n")
  const blocks: Block[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index] ?? ""

    const fence = FENCE.exec(line)
    if (fence) {
      const marker = fence[1]!
      const lang = fence[2] ?? ""
      const body: string[] = []
      index++
      while (index < lines.length && !(lines[index] ?? "").trimStart().startsWith(marker)) {
        body.push(lines[index] ?? "")
        index++
      }
      index++ // closing fence (or EOF)
      // Models love to leave a blank first/last line inside the fence; the
      // padding shows up as extra empty rows in the code card.
      while (body.length && !body[0]!.trim()) body.shift()
      while (body.length && !body[body.length - 1]!.trim()) body.pop()
      blocks.push({ kind: "code", lang, text: body.join("\n") })
      continue
    }

    if (!line.trim()) {
      index++
      continue
    }

    if (RULE.test(line)) {
      blocks.push({ kind: "hr" })
      index++
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1]!.length, text: heading[2]!.trim() })
      index++
      continue
    }

    if (line.trimStart().startsWith("|")) {
      const rows: string[] = []
      while (index < lines.length && lines[index]?.trimStart().startsWith("|")) {
        rows.push(lines[index]?.trim() ?? "")
        index++
      }
      blocks.push({ kind: "table", text: rows.join("\n") })
      continue
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      const ordered = ORDERED.test(line)
      const first = ordered ? ORDERED.exec(line) : null
      // The literal first number is kept so numbering survives the list being
      // split by interleaved code blocks (1 … code … 2 stays 1, 2).
      const start = first ? Number.parseInt(first[1]!, 10) : 1
      const items: string[] = []
      while (index < lines.length) {
        const current = lines[index] ?? ""
        const match = ordered ? ORDERED.exec(current) : BULLET.exec(current)
        if (!match) break
        items.push((ordered ? match[2] : match[1])!.trim())
        index++
      }
      blocks.push({ kind: "list", ordered, start, items })
      continue
    }

    const quote = QUOTE.exec(line)
    if (quote) {
      const parts: string[] = [quote[1]!]
      index++
      while (index < lines.length && QUOTE.test(lines[index] ?? "")) {
        const q = QUOTE.exec(lines[index] ?? "")
        if (q) parts.push(q[1]!)
        index++
      }
      blocks.push({ kind: "quote", text: parts.join("\n") })
      continue
    }

    // Paragraph: consecutive plain lines.
    const paragraph: string[] = [line]
    index++
    while (index < lines.length) {
      const next = lines[index] ?? ""
      if (
        !next.trim() ||
        FENCE.test(next) ||
        HEADING.test(next) ||
        RULE.test(next) ||
        BULLET.test(next) ||
        ORDERED.test(next) ||
        QUOTE.test(next) ||
        next.trimStart().startsWith("|")
      ) {
        break
      }
      paragraph.push(next)
      index++
    }
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") })
  }

  return blocks
}

/** Split inline markdown into styled segments. */
export function inline(text: string): Segment[] {
  const segments: Segment[] = []
  let plain = ""
  let index = 0

  const flush = () => {
    if (plain) {
      segments.push({ text: plain })
      plain = ""
    }
  }

  while (index < text.length) {
    if (text.startsWith("**", index)) {
      const end = text.indexOf("**", index + 2)
      if (end > index + 2) {
        flush()
        segments.push({ text: text.slice(index + 2, end), bold: true })
        index = end + 2
        continue
      }
    }

    if (text[index] === "`") {
      const end = text.indexOf("`", index + 1)
      if (end > index + 1) {
        flush()
        segments.push({ text: text.slice(index + 1, end), code: true })
        index = end + 1
        continue
      }
    }

    if (text[index] === "[") {
      const labelEnd = text.indexOf("](", index)
      const urlEnd = labelEnd === -1 ? -1 : text.indexOf(")", labelEnd + 2)
      if (labelEnd !== -1 && urlEnd !== -1) {
        flush()
        segments.push({ text: text.slice(index + 1, labelEnd), link: true })
        index = urlEnd + 1
        continue
      }
    }

    if (text[index] === "*" || text[index] === "_") {
      const marker = text[index]!
      const end = text.indexOf(marker, index + 1)
      if (end > index + 1) {
        // CommonMark flanking rule: an emphasis run must not start or end
        // inside a word (prevents `snake_case_ids` and URLs with `_` from
        // turning into broken italics).
        const before = text[index - 1] ?? ""
        const after = text[end + 1] ?? ""
        const opens = !before || /[^A-Za-z0-9]/.test(before)
        const closes = !after || /[^A-Za-z0-9]/.test(after)
        if (opens && closes) {
          flush()
          segments.push({ text: text.slice(index + 1, end), italic: true })
          index = end + 1
          continue
        }
      }
    }

    plain += text[index]
    index++
  }

  flush()
  return segments
}